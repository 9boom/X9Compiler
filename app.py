import re
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

def modify_index_html(build_dir, base_url, auth_token):
    """Modify index.html to use B2 URLs and apply custom styling"""
    index_path = os.path.join(build_dir, 'index.html')
    
    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # ==================== CSS Replacement ====================
    new_style = """<style>
    /* Custom spinner styles */
    body {
        margin: 0;
        padding: 0;
        background-color: #ffa500;
        font-family: Arial, sans-serif;
    }

    #transfer,
    #status,
    #progress {
        display: none !important;
    }

    canvas.emscripten {
        position: absolute;
        top: 0;
        bottom: 0;
        left: 0;
        right: 0;
        width: 100%;
        height: 100%;
        background-color: transparent;
        z-index: 5;
    }

    .spinnerContainer {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        text-align: center;
        color: white;
    }

    .spinner {
        width: 80px;
        height: 80px;
        border: 8px solid #333;
        border-top: 8px solid #ff9900;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin: 0 auto;
    }

    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }

    .loadingText {
        margin-top: 15px;
        font-size: 18px;
        color: white;
    }

    /* Preserve original styles */
    div.emscripten { text-align: center; }
    div.emscripten_border { border: 1px solid black; }
    div.thick_border { border: 4px solid black; }
    .topright { position:absolute; top:0px; right:0px; }
    .bottomright { position:absolute; top: 40%; right: 0px; }
    .center { display: flex; align-items: center; justify-content: center; }
    .trinfo { position:relative; right:0px; border: 1px solid black; }
    .framed { position:relative; top:150px; right:10px; border: 1px solid black; }
    </style>"""

    # Replace entire style block
    content = re.sub(r'<style>.*?</style>', new_style, content, flags=re.DOTALL)

    # ==================== HTML Injection ====================
    spinner_html = """
    <!-- Custom spinner -->
    <div class="spinnerContainer" id="spinnerContainer">
        <div class="spinner"></div>
        <div class="loadingText">Downloading... Please wait until the Start button appears or game started.</div>
    </div>"""

    # Insert spinner after opening body tag
    content = content.replace('<body>', '<body>\n' + spinner_html)

    # ==================== JavaScript Functions ====================
    new_js_functions = """
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
        // Preserve original keydown handler
        window.addEventListener("keydown", function(e) {
            if(["Space","ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].indexOf(e.code) > -1) {
                if (!python.config.debug)
                    e.preventDefault();
            }
        }, false);
    }"""

    # Replace JS functions with updated versions
    content = re.sub(
        r'(async function custom_onload\(.*?})(.*?function custom_prerun\(.*?})(.*?function custom_postrun\(.*?})',
        new_js_functions,
        content,
        flags=re.DOTALL
    )

    # ==================== Python Code Modifications ====================
    # Update progress bar colors
    content = content.replace(
        'pygame.draw.rect(screen,(10,10,10),( marginx-ux(10), marginy-uy(10), (total*slot)+ux(20), uy(110) ) )',
        'pygame.draw.rect(screen,(255,165,10),( marginx-ux(10), marginy-uy(10), (total*slot)+ux(20), uy(110) ) )'
    )
    content = content.replace(
        'pygame.draw.rect(screen,(0,255,0), ( marginx, marginy, track.pos*slot, uy(90)) )',
        'pygame.draw.rect(screen,(25,25,25), ( marginx, marginy, track.pos*slot, uy(90)) )'
    )

    # ==================== URL Replacements ====================
    # Replace favicon URL
    content = content.replace(
        '<link rel="icon" type="image/png" href="favicon.png" sizes="16x16">',
        f'<link rel="icon" type="image/png" href="{base_url}favicon.png?Authorization={auth_token}" sizes="16x16">'
    )

    # Replace APK URL
    content = re.sub(
        r'apk = ".*?.apk"',
        f'apk = "{base_url}project.apk?Authorization={auth_token}"',
        content
    )

    # Save modified content
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(content)

@app.route('/upload', methods=['POST'])
def upload_game():
    if 'project' not in request.files:
        return jsonify({'error': 'No project zip uploaded'}), 400
    
    project_file = request.files['project']
    if not project_file.filename.endswith('.zip'):
        return jsonify({'error': 'Invalid file type'}), 400

    # Get game data from form
    game_data = {field: request.form.get(field) for field in [
        'name', 'description', 'release_date', 
        'version', 'code_version', 'genre', 'hashtags'
    ]}
    
    # Create game ID and set expiration
    game_id = str(uuid.uuid4())
    valid_duration = 7 * 24 * 3600  # 7 days
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Process project with Pygbag
            build_dir = process_project(project_file, tmpdir)
            project_prefix = f"built_games/{game_id}/"
            base_url = f"https://f005.backblazeb2.com/file/{bucket_name}/{project_prefix}"

            # Create Auth Token
            auth_token = bucket.get_download_authorization(
                file_name_prefix=project_prefix,
                valid_duration_in_seconds=valid_duration
            )

            # Modify index.html
            modify_index_html(build_dir, base_url, auth_token)

            # Upload all files in build directory
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

            # Upload logo
            if 'logo' in request.files:
                logo_file = request.files['logo']
                logo_filename = f"logos/{game_id}.png"
                bucket.upload_bytes(
                    logo_file.read(),
                    logo_filename,
                    file_infos={"game_id": game_id},
                    cache_control="public, max-age=31536000"
                )
                auth_token = bucket.get_download_authorization(
                    file_name_prefix=logo_filename,
                    valid_duration_in_seconds=valid_duration
                )
                game_data['logo_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{logo_filename}?Authorization={auth_token}"

            # Upload home screen
            if 'home_screen' in request.files:
                home_screen_file = request.files['home_screen']
                hs_filename = f"home_screens/{game_id}.png"
                bucket.upload_bytes(
                    home_screen_file.read(),
                    hs_filename,
                    file_infos={"game_id": game_id},
                    cache_control="public, max-age=31536000"
                )
                auth_token = bucket.get_download_authorization(
                    file_name_prefix=hs_filename,
                    valid_duration_in_seconds=valid_duration
                )
                game_data['home_screen_url'] = f"https://f005.backblazeb2.com/file/{bucket_name}/{hs_filename}?Authorization={auth_token}"

            # Save data to Firestore
            db.collection('games').document(game_id).set(game_data)
            
            return jsonify({
                'success': True,
                'game_id': game_id,
                'project_url': game_data['project_url'],
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
