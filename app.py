import os
import tempfile
import zipfile
import uuid
import subprocess
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from b2sdk.v2 import InMemoryAccountInfo, B2Api

# Initialize Firebase Admin
cred = credentials.Certificate('x9engine-firebase-adminsdk-fbsvc-36d1671cdb.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize Backblaze B2 client
info = InMemoryAccountInfo()
b2_api = B2Api(info)
b2_api.authorize_account("production", os.environ.get("B2_KEY_ID"), os.environ.get("B2_APPLICATION_KEY"))
bucket_name = "X9Engine"
bucket = b2_api.get_bucket_by_name(bucket_name)

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

def process_project(project_file, tmpdir):
    """Process project zip with Pygbag and return the build directory path"""
    # Save uploaded zip
    project_zip_path = os.path.join(tmpdir, 'project.zip')
    project_file.save(project_zip_path)
    
    # Extract project
    project_dir = os.path.join(tmpdir, 'project')
    os.makedirs(project_dir, exist_ok=True)
    with zipfile.ZipFile(project_zip_path, 'r') as zip_ref:
        zip_ref.extractall(project_dir)
    
    # Run pygbag build
    result = subprocess.run(
        ['pygbag', '--build', project_dir],
        capture_output=True,
        text=True,
        check=True
    )
    
    # Verify build output
    build_dir = os.path.join(project_dir, 'build', 'web')
    if not os.path.exists(build_dir):
        raise RuntimeError("Pygbag build failed to generate output files")
    
    return build_dir

@app.route('/upload', methods=['POST'])
def upload_game():
    if 'project' not in request.files:
        return jsonify({'error': 'No project zip uploaded'}), 400
    
    project_file = request.files['project']
    if not project_file.filename.endswith('.zip'):
        return jsonify({'error': 'Invalid file type'}), 400

    # ดึงข้อมูลเกมจากฟอร์ม
    game_data = {field: request.form.get(field) for field in [
        'name', 'description', 'release_date', 
        'version', 'code_version', 'genre', 'hashtags'
    ]}
    
    # สร้าง ID และกำหนดเวลาหมดอายุ
    game_id = str(uuid.uuid4())
    valid_duration = 7 * 24 * 3600  # 7 วัน
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # ประมวลผลโปรเจคด้วย Pygbag และรับ path ของ build directory
            build_dir = process_project(project_file, tmpdir)
            
            # อัปโหลดไฟล์ใน build directory ทีละไฟล์
            project_prefix = f"built_games/{game_id}/"
            for root, _, files in os.walk(build_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # คำนวณ relative path ภายใน build_dir
                    relative_path = os.path.relpath(file_path, build_dir)
                    # สร้าง B2 key โดยใช้ forward slash
                    b2_key = project_prefix + relative_path.replace("\\", "/")
                    with open(file_path, 'rb') as f:
                        file_content = f.read()
                    bucket.upload_bytes(
                        file_content, 
                        b2_key, 
                        file_infos={"game_id": game_id}
                    )
            
            # สร้าง signed URL สำหรับไฟล์ entry point (index.html)
            auth_token = bucket.get_download_authorization(
                file_name_prefix=project_prefix, 
                valid_duration_in_seconds=valid_duration
            )
            game_data['project_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{project_prefix}index.html?Authorization={auth_token}"

            # อัปโหลดโลโก้
            if 'logo' in request.files:
                logo_file = request.files['logo']
                logo_filename = f"logos/{game_id}.png"
                bucket.upload_bytes(
                    logo_file.read(),
                    logo_filename,
                    file_infos={"game_id": game_id}
                )
                auth_token = bucket.get_download_authorization(
                    file_name_prefix=logo_filename,
                    valid_duration_in_seconds=valid_duration
                )
                game_data['logo_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{logo_filename}?Authorization={auth_token}"

            # อัปโหลดหน้าจอแรก
            if 'home_screen' in request.files:
                home_screen_file = request.files['home_screen']
                hs_filename = f"home_screens/{game_id}.png"
                bucket.upload_bytes(
                    home_screen_file.read(),
                    hs_filename,
                    file_infos={"game_id": game_id}
                )
                auth_token = bucket.get_download_authorization(
                    file_name_prefix=hs_filename,
                    valid_duration_in_seconds=valid_duration
                )
                game_data['home_screen_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{hs_filename}?Authorization={auth_token}"

            # บันทึกข้อมูลลง Firestore
            db.collection('games').document(game_id).set(game_data)
            
            return jsonify({
                'success': True,
                'game_id': game_id,
                'build_log': 'Build successful'
            }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            'error': f'Build failed: {e.stderr}',
            'returncode': e.returncode
        }), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
