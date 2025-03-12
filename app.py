import os
import tempfile
import zipfile
import uuid
from io import BytesIO
from datetime import timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from b2sdk.v2 import InMemoryAccountInfo, B2Api

# Initialize Firebase Admin (สำหรับ Firestore)
cred = credentials.Certificate('x9engine-firebase-adminsdk-fbsvc-36d1671cdb.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize Backblaze B2 client
info = InMemoryAccountInfo()
b2_api = B2Api(info)
b2_api.authorize_account("production", os.environ.get("B2_KEY_ID"), os.environ.get("B2_APPLICATION_KEY"))
bucket_name = "X9Engine"  # ปรับเป็นชื่อ bucket ของคุณ
bucket = b2_api.get_bucket_by_name(bucket_name)

app = Flask(__name__)
CORS(app)  # อนุญาต CORS สำหรับทุก origin
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # จำกัดไฟล์ 100MB

@app.route('/upload', methods=['POST'])
def upload_game():
    # ตรวจสอบไฟล์โปรเจค zip ที่ส่งมาด้วย key "project"
    if 'project' not in request.files:
        return jsonify({'error': 'No project zip uploaded'}), 400
    project_file = request.files['project']
    if project_file.filename == '' or not project_file.filename.endswith('.zip'):
        return jsonify({'error': 'Invalid file uploaded. Please upload a .zip file'}), 400

    # รับ Game Info จาก form data
    name = request.form.get('name')
    description = request.form.get('description')
    release_date = request.form.get('release_date')
    version = request.form.get('version')
    code_version = request.form.get('code_version')
    genre = request.form.get('genre')
    hashtags = request.form.get('hashtags')

    # รับไฟล์โลโก้และหน้าจอแรก (ถ้ามี)
    logo_file = request.files.get('logo')
    home_screen_file = request.files.get('home_screen')

    # สร้าง unique id สำหรับเกมนี้
    game_id = str(uuid.uuid4())
    valid_duration = 7 * 24 * 3600  # 7 วัน (ในหน่วยวินาที)

    try:
        # อัปโหลดไฟล์โปรเจคไปยัง Backblaze B2
        project_content = project_file.read()
        project_filename = f"project_files/{game_id}.zip"
        bucket.upload_bytes(project_content, project_filename, file_infos={"game_id": game_id})
        # สร้าง signed URL สำหรับดาวน์โหลดโปรเจค
        auth_token = bucket.get_download_authorization(file_name_prefix=project_filename, valid_duration_in_seconds=valid_duration)
        project_url = f"https://f005.backblazeb2.com/file/{bucket_name}/{project_filename}?Authorization={auth_token}"

        # อัปโหลดโลโก้ (ถ้ามี)
        logo_url = None
        if logo_file:
            logo_content = logo_file.read()
            logo_filename = f"logos/{game_id}.png"
            bucket.upload_bytes(logo_content, logo_filename, file_infos={"game_id": game_id})
            auth_token_logo = bucket.get_download_authorization(file_name_prefix=logo_filename, valid_duration_in_seconds=valid_duration)
            logo_url = f"https://f005.backblazeb2.com/file/{bucket_name}/{logo_filename}?Authorization={auth_token_logo}"

        # อัปโหลด home screen (ถ้ามี)
        home_screen_url = None
        if home_screen_file:
            hs_content = home_screen_file.read()
            hs_filename = f"home_screens/{game_id}.png"
            bucket.upload_bytes(hs_content, hs_filename, file_infos={"game_id": game_id})
            auth_token_hs = bucket.get_download_authorization(file_name_prefix=hs_filename, valid_duration_in_seconds=valid_duration)
            home_screen_url = f"https://f005.backblazeb2.com/file/{bucket_name}/{hs_filename}?Authorization={auth_token_hs}"

        # บันทึก Game Info ลง Firestore
        game_data = {
            'name': name,
            'description': description,
            'release_date': release_date,
            'version': version,
            'code_version': code_version,
            'genre': genre,
            'hashtags': hashtags,
            'project_url': project_url,
            'logo_url': logo_url,
            'home_screen_url': home_screen_url
        }
        db.collection('games').document(game_id).set(game_data)

        return jsonify({'success': True, 'game_id': game_id}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
