import os
import re
import tempfile
import zipfile
import uuid
import subprocess
from bs4 import BeautifulSoup
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

def validate_project_structure(project_dir):
    """Validate essential project files (แก้ไขแล้ว)"""
    required_files = [
        'main.py',       # ตรวจสอบไฟล์หลัก
        'assets/'        # ตรวจสอบโฟลเดอร์ assets
    ]
    
    for file in required_files:
        path = os.path.join(project_dir, file)
        if not os.path.exists(path):
            if file.endswith('/'):
                if not os.path.isdir(path):
                    raise ValueError(f"Missing directory: {file}")
            else:
                raise ValueError(f"Missing file: {file}")

def process_project(project_file, tmpdir):
    """Process project zip with Pygbag (แก้ไขแล้ว)"""
    project_zip_path = os.path.join(tmpdir, 'project.zip')
    project_file.save(project_zip_path)
    
    # Extract project
    project_dir = os.path.join(tmpdir, 'project')
    os.makedirs(project_dir, exist_ok=True)
    
    with zipfile.ZipFile(project_zip_path, 'r') as zip_ref:
        zip_ref.extractall(project_dir)
    
    # Validate project structure (ตรวจสอบก่อน build)
    validate_project_structure(project_dir)
    
    # Build with pygbag
    build_dir = os.path.join(project_dir, 'build')
    os.makedirs(build_dir, exist_ok=True)
    
    result = subprocess.run(
        ['pygbag', '--build', '--template', '0.9.0', project_dir],
        cwd=build_dir,
        capture_output=True,
        text=True,
        check=True
    )
    
    # ตรวจสอบ build output หลัง build เสร็จสิ้น
    web_dir = os.path.join(build_dir, 'web')
    index_path = os.path.join(web_dir, 'index.html')
    if not os.path.exists(index_path):
        raise RuntimeError(
            f"Build failed - Missing index.html\n"
            f"Pygbag output:\n{result.stdout}\n{result.stderr}"
        )
    
    return web_dir

def modify_index_html(build_dir, base_url, auth_token):
    """Modify index.html with BeautifulSoup"""
    index_path = os.path.join(build_dir, 'index.html')
    
    with open(index_path, 'r+', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

        # Remove existing styles
        for style in soup.find_all('style'):
            style.decompose()

        # Add new styles
        new_style = BeautifulSoup("""
        <style>
            body { margin:0; padding:0; background:#ffa500; font-family:Arial, sans-serif; }
            #transfer, #status, #progress { display:none!important; }
            canvas.emscripten { 
                position:absolute; 
                top:0; 
                bottom:0; 
                left:0; 
                right:0; 
                width:100%; 
                height:100%; 
                z-index:5; 
                background-color:transparent; 
            }
            .spinnerContainer {
                position:absolute;
                top:50%;
                left:50%;
                transform:translate(-50%,-50%);
                text-align:center;
                color:white;
            }
            .spinner {
                width:80px;
                height:80px;
                border:8px solid #333;
                border-top:8px solid #ff9900;
                border-radius:50%;
                animation:spin 1s linear infinite;
                margin:0 auto;
            }
            @keyframes spin {
                0% { transform:rotate(0deg); }
                100% { transform:rotate(360deg); }
            }
            .loadingText {
                margin-top:15px;
                font-size:18px;
                color:white;
            }
        </style>
        """, 'html.parser')
        soup.head.append(new_style)

        # Add spinner container
        spinner_div = BeautifulSoup("""
        <div class="spinnerContainer" id="spinnerContainer">
            <div class="spinner"></div>
            <div class="loadingText">Downloading... Please wait until the Start button appears or game starts.</div>
        </div>
        """, 'html.parser')
        soup.body.insert(0, spinner_div)

        # Update JavaScript functions
        script_tag = soup.find('script', string=re.compile(r'function custom_onload'))
        if script_tag:
            new_js = """
            async function custom_onload(debug_hidden) {
                console.log(__FILE__, "custom_onload");
                pyconsole.hidden = debug_hidden;
                system.hidden = debug_hidden;
                transfer.hidden = debug_hidden;
                info.hidden = debug_hidden;
                box.hidden = debug_hidden;
                document.getElementById("spinnerContainer").style.display = debug_hidden ? "none" : "block";
            }

            function custom_prerun() {
                console.log(__FILE__, "custom_prerun");
                document.getElementById("spinnerContainer").style.display = "block";
            }

            function custom_postrun() {
                console.log(__FILE__, "custom_postrun");
                document.getElementById("spinnerContainer").style.display = "none";
                window.addEventListener("keydown", function(e) {
                    if(["Space","ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].indexOf(e.code) > -1) {
                        if (!python.config.debug) e.preventDefault();
                    }
                }, false);
            }

            function debug() {
                python.config.debug = true;
                custom_onload(false);
                Module.PyRun_SimpleString("shell.uptime()");
                window_resize();
            }
            """
            script_tag.string = new_js

        # Update Python code in script
        python_script = soup.find('script', id='site')
        if python_script:
            code = python_script.string
            code = code.replace('platform.document.body.style.background = "#7f7f7f"', 
                              'platform.document.body.style.background = "#ffa500"')
            code = code.replace('pygame.draw.rect(screen,(10,10,10),( marginx-ux(10), marginy-uy(10), (total*slot)+ux(20), uy(110) )',
                              'pygame.draw.rect(screen,(255,165,10),( marginx-ux(10), marginy-uy(10), (total*slot)+ux(20), uy(110) )')
            code = code.replace('pygame.draw.rect(screen,(0,255,0), ( marginx, marginy, track.pos*slot, uy(90)) )',
                              'pygame.draw.rect(screen,(25,25,25), ( marginx, marginy, track.pos*slot, uy(90)) )')
            code = re.sub(r'apk\s*=\s*"project\.apk"', 
                        f'apk = "{base_url}project.apk?Authorization={auth_token}"', code)
            python_script.string = code

        # Update favicon link
        favicon = soup.find('link', rel='icon')
        if favicon:
            favicon['href'] = f"{base_url}favicon.png?Authorization={auth_token}"

        # Save changes
        f.seek(0)
        f.write(str(soup))
        f.truncate()

@app.route('/upload', methods=['POST'])
def upload_game():
    if 'project' not in request.files:
        return jsonify({'error': 'No project zip uploaded'}), 400
    
    project_file = request.files['project']
    if not project_file.filename.endswith('.zip'):
        return jsonify({'error': 'Invalid file type'}), 400

    game_data = {
        'name': request.form.get('name'),
        'description': request.form.get('description'),
        'release_date': request.form.get('release_date'),
        'version': request.form.get('version'),
        'code_version': request.form.get('code_version'),
        'genre': request.form.get('genre'),
        'hashtags': request.form.get('hashtags')
    }

    game_id = str(uuid.uuid4())
    valid_duration = 7 * 24 * 3600  # 7 days

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Process project
            build_dir = process_project(project_file, tmpdir)
            project_prefix = f"built_games/{game_id}/"
            base_url = f"https://f005.backblazeb2.com/file/{bucket_name}/{project_prefix}"

            # Generate auth token
            auth_token = bucket.get_download_authorization(
                file_name_prefix=project_prefix,
                valid_duration_in_seconds=valid_duration
            )

            # Modify index.html
            modify_index_html(build_dir, base_url, auth_token)

            # Upload build files
            for root, _, files in os.walk(build_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, build_dir)
                    b2_key = project_prefix + relative_path.replace("\\", "/")
                    
                    with open(file_path, 'rb') as f:
                        bucket.upload_bytes(
                            f.read(),
                            b2_key,
                            file_infos={"game_id": game_id},
                            cache_control="public, max-age=31536000"
                        )

            # Set project URL
            game_data['project_url'] = f"{base_url}index.html?Authorization={auth_token}"

            # Handle logo upload
            if 'logo' in request.files:
                logo_file = request.files['logo']
                logo_filename = f"logos/{game_id}.png"
                bucket.upload_bytes(
                    logo_file.read(),
                    logo_filename,
                    file_infos={"game_id": game_id},
                    cache_control="public, max-age=31536000"
                )
                game_data['logo_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{logo_filename}?Authorization={auth_token}"

            # Handle home screen upload
            if 'home_screen' in request.files:
                hs_file = request.files['home_screen']
                hs_filename = f"home_screens/{game_id}.png"
                bucket.upload_bytes(
                    hs_file.read(),
                    hs_filename,
                    file_infos={"game_id": game_id},
                    cache_control="public, max-age=31536000"
                )
                game_data['home_screen_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{hs_filename}?Authorization={auth_token}"

            # Save to Firestore
            db.collection('games').document(game_id).set(game_data)
            
            return jsonify({
                'success': True,
                'game_id': game_id,
                'project_url': game_data['project_url'],
                'expires_in': valid_duration
            }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            'error': f'Build failed: {e.stderr}',
            'returncode': e.returncode
        }), 500
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
