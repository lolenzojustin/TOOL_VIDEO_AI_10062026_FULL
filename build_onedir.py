import os
import sys
import shutil
import subprocess
import zipfile
import glob
import json
import time
import stat
import requests

def remove_dir(path):
    """Safely remove a directory, retrying and handling permissions/locks gracefully."""
    if not os.path.exists(path):
        return
        
    def on_rm_error(func, error_path, exc_info):
        try:
            os.chmod(error_path, stat.S_IWRITE)
            func(error_path)
        except Exception:
            pass

    for attempt in range(5):
        try:
            shutil.rmtree(path, onerror=on_rm_error)
            return
        except Exception as e:
            print(f"[!] Warning: Failed to remove directory {path} (attempt {attempt+1}): {e}")
            time.sleep(1)
            
    shutil.rmtree(path, ignore_errors=True)

def build_onedir():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(project_dir, "dist")
    build_dir = os.path.join(project_dir, "build")
    
    # Read version dynamically from api_check_version.py
    version = "1.0.13" # default fallback
    try:
        sys.path.insert(0, project_dir)
        import api_check_version
        version = api_check_version.APP_VERSION
    except Exception as e:
        print(f"[!] Error importing api_check_version: {e}")
        
    print(f"[*] Target build version: {version}")

    # Query Server Version from API to warn if there's a mismatch
    server_version = None
    try:
        api_url = "https://thangdz.com/api/categories/48576c9c-6da3-4690-885c-71b18ac49c3d"
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            server_version = data.get("version", "").strip()
            print(f"[+] Server version from API: {server_version}")
        else:
            print(f"[!] Warning: Server API returned HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] Warning: Failed to connect to server API: {e}")
        
    if server_version:
        try:
            curr_tuple = tuple(map(int, version.split('.')))
            serv_tuple = tuple(map(int, server_version.split('.')))
            if serv_tuple > curr_tuple:
                print(f"\n[WARNING] DETECTED VERSION MISMATCH!")
                print(f"  Server Version on Web is: {server_version}")
                print(f"  APP_VERSION inside code is: {version}")
                print(f"  Since Server Version > APP_VERSION, the app will trigger an immediate update dialog")
                print(f"  after startup and cause an update loop when the update download completes!")
                print(f"  Please update APP_VERSION in code or update the API server version before proceeding.\n")
            elif serv_tuple == curr_tuple:
                print(f"[+] Version match check passed (both are {version}). No update loops will occur.")
            else:
                print(f"[+] APP_VERSION ({version}) is ahead of server version ({server_version}). OK.")
        except Exception as e:
            print(f"[!] Version comparison parsing error: {e}")

    # 1. Clean previous build files: build, dist, AI_Video_Tool.spec, AI_Video_Tool_v*.zip, root AI_Video_Tool.exe
    print("[-] Cleaning up old build files and previous ZIP packages...")
    remove_dir(dist_dir)
    remove_dir(build_dir)
    
    root_exe_path = os.path.join(project_dir, "AI_Video_Tool.exe")
    if os.path.exists(root_exe_path):
        try:
            os.remove(root_exe_path)
            print("[-] Removed old root executable.")
        except Exception as e:
            print(f"[!] Warning: Failed to remove old root executable: {e}")
        
    spec_path = os.path.join(project_dir, "AI_Video_Tool.spec")
    if os.path.exists(spec_path):
        try:
            os.remove(spec_path)
            print("[-] Removed old spec file.")
        except Exception as e:
            print(f"[!] Warning: Failed to remove spec file: {e}")
        
    for zip_file in glob.glob(os.path.join(project_dir, "AI_Video_Tool_v*.zip")):
        print(f"[-] Removing old ZIP: {zip_file}")
        try:
            os.remove(zip_file)
        except Exception as e:
            print(f"[!] Error removing old ZIP {zip_file}: {e}")

    # 2. Run PyInstaller
    python_bin = os.path.join(project_dir, "venv", "Scripts", "python.exe")
    if not os.path.exists(python_bin):
        python_bin = sys.executable
        
    cmd = [
        python_bin,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noupx",
        "--onedir",
        "--windowed",
        "--name=AI_Video_Tool",
        "giaodientoolmau_videoai.py"
    ]
    
    print(f"[*] Running PyInstaller command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=project_dir)
    
    if result.returncode != 0:
        print("[!] PyInstaller failed to compile the application.")
        sys.exit(1)
        
    compiled_app_dir = os.path.join(dist_dir, "AI_Video_Tool")
    exe_path = os.path.join(compiled_app_dir, "AI_Video_Tool.exe")
    if not os.path.exists(exe_path):
        print(f"[!] Executable not found at expected path: {exe_path}")
        sys.exit(1)
        
    print("[+] PyInstaller compiled the tool successfully in onedir format.")

    # 3. Create packaging folder: AI_Video_Tool_v{VERSION}
    pkg_dir_name = f"AI_Video_Tool_v{version}"
    dist_pkg_dir = os.path.join(project_dir, pkg_dir_name)
    remove_dir(dist_pkg_dir)
    os.makedirs(dist_pkg_dir, exist_ok=True)
    
    # Copy EXE and _internal
    print("[-] Copying compiled executable and runtimes to packaging folder...")
    shutil.copy2(exe_path, os.path.join(dist_pkg_dir, "AI_Video_Tool.exe"))
    shutil.copytree(os.path.join(compiled_app_dir, "_internal"), os.path.join(dist_pkg_dir, "_internal"))
    
    # Write clean config.env
    print("[-] Generating clean config.env...")
    clean_config_content = f"""# Auto-generated config for tool UI values
AI_MODEL=Gemini 3.1 Flash Lite
ASYNC_API_KEY=
API_URL_GPM=http://localhost:9495
BROWSER=GPM Global
WIN_SIZE=800px:800px
VIDEO_FOLDER=
STYLE=Hyper Realistic (Chân thực 100%)
LANGUAGE=us English
COPY_RATIO=50% - Copy một nửa
SCENE_COUNT=10
VOICE_DESC=
PROXY_LIST=
GROK_LANG=Tiếng Việt (Vietnamese)
GROK_LINK=
GROK_DESC=
VEO3_LANG=Tiếng Việt (Vietnamese)
VEO3_LINK=
VEO3_DESC=
SEED_LANG=Tiếng Việt (Vietnamese)
SEED_LINK=
SEED_DESC=
KOL_LANG=Tiếng Việt (Vietnamese)
KOL_DESC=
KOL_PROMPT=
FLOW_CONTENT_TYPE=Video
FLOW_FRAME_TYPE=Khung hình
FLOW_ASPECT_RATIO=9:16
FLOW_GEN_COUNT=1x
FLOW_AI_MODEL=Veo 3.1 - Lite
KOL_AI_LANG=Tiếng Việt (Vietnamese)
KOL_AI_REF_IMAGE=
KOL_AI_PRODUCT_IMAGE=
KOL_AI_DESC=
VERSION={version}
CURRENT_TAB=0
"""
    with open(os.path.join(dist_pkg_dir, "config.env"), "w", encoding="utf-8") as f:
        f.write(clean_config_content)
        
    # Write empty license.json
    print("[-] Generating empty license.json...")
    with open(os.path.join(dist_pkg_dir, "license.json"), "w", encoding="utf-8") as f:
        f.write('{\n    "license_key": ""\n}')

    # 4. Package into ZIP
    zip_filename = f"{pkg_dir_name}.zip"
    zip_filepath = os.path.join(project_dir, zip_filename)
    if os.path.exists(zip_filepath):
        try:
            os.remove(zip_filepath)
        except Exception as e:
            print(f"[!] Warning: Failed to remove old ZIP: {e}")
        
    print(f"[*] Packaging files into {zip_filename}...")
    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(dist_pkg_dir):
            for file in files:
                file_abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_abs_path, project_dir).replace('\\', '/')
                zip_file.write(file_abs_path, rel_path)
                
    # Clean up temporary packaging directory
    remove_dir(dist_pkg_dir)
    print(f"[+] Package ZIP created successfully at: {zip_filepath}")

    # 5. Extract and Verify ZIP
    print("[*] Extracting and verifying ZIP contents...")
    test_extract_dir = os.path.join(project_dir, "test_extraction")
    remove_dir(test_extract_dir)
    os.makedirs(test_extract_dir, exist_ok=True)
    
    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
        zip_ref.extractall(test_extract_dir)
        
    # Perform checklists
    unzipped_root = os.path.join(test_extract_dir, pkg_dir_name)
    has_exe = os.path.exists(os.path.join(unzipped_root, "AI_Video_Tool.exe"))
    has_internal = os.path.exists(os.path.join(unzipped_root, "_internal"))
    has_config = os.path.exists(os.path.join(unzipped_root, "config.env"))
    has_license = os.path.exists(os.path.join(unzipped_root, "license.json"))
    
    # Check EXE file size
    exe_size_ok = True
    exe_size_mb = 0
    if has_exe:
        exe_path_unzipped = os.path.join(unzipped_root, "AI_Video_Tool.exe")
        exe_size_mb = os.path.getsize(exe_path_unzipped) / (1024 * 1024)
        if exe_size_mb > 50:  # Bootloader should be small, definitely < 50MB
            exe_size_ok = False
            print(f"[!] Warning: AI_Video_Tool.exe is too large ({exe_size_mb:.2f} MB). It might be a onefile EXE copied by mistake!")
            
    # Check python runtime dll in _internal
    has_python_dll = False
    if has_internal:
        internal_dir = os.path.join(unzipped_root, "_internal")
        for f in os.listdir(internal_dir):
            if f.lower().startswith("python") and f.lower().endswith(".dll"):
                has_python_dll = True
                print(f"[+] Found Python runtime DLL: {f}")
                break
    
    # Check for excluded files/directories
    has_py_files = False
    has_venv = False
    has_build = False
    has_dist_inside = False
    has_git = False
    
    for root, dirs, files in os.walk(test_extract_dir):
        for d in dirs:
            if d == "venv":
                has_venv = True
            if d == "build":
                has_build = True
            if d == "dist":
                has_dist_inside = True
            if d == ".git":
                has_git = True
        for f in files:
            if f.endswith(".py"):
                has_py_files = True
                print(f"[!] Warning: Found Python source file: {os.path.join(root, f)}")
                
    # Verify license key content is empty
    license_key_valid = False
    if has_license:
        with open(os.path.join(unzipped_root, "license.json"), "r", encoding="utf-8") as f:
            try:
                lic_data = json.load(f)
                license_key_valid = lic_data.get("license_key") == ""
            except Exception as e:
                print(f"[!] License json verification exception: {e}")
                
    print("\n--- ZIP VERIFICATION REPORT ---")
    print(f"1. No python (.py) source files: {'PASSED' if not has_py_files else 'FAILED'}")
    print(f"2. AI_Video_Tool.exe present: {'PASSED' if has_exe else 'FAILED'}")
    print(f"3. AI_Video_Tool.exe size < 50MB: {'PASSED' if exe_size_ok else 'FAILED'} ({exe_size_mb:.2f} MB)")
    print(f"4. _internal runtime folder present: {'PASSED' if has_internal else 'FAILED'}")
    print(f"5. Python runtime DLL inside _internal: {'PASSED' if has_python_dll else 'FAILED'}")
    print(f"6. Clean config.env present: {'PASSED' if has_config else 'FAILED'}")
    print(f"7. Empty license.json present & verified: {'PASSED' if (has_license and license_key_valid) else 'FAILED'}")
    print(f"8. Excluded venv/build/dist/.git: {'PASSED' if not (has_venv or has_build or has_dist_inside or has_git) else 'FAILED'}")
    print("--------------------------------\n")
    
    if has_py_files or not (has_exe and exe_size_ok and has_internal and has_python_dll and has_config and has_license and license_key_valid):
        print("[!] Verification failed. ZIP package is incomplete or invalid.")
        remove_dir(test_extract_dir)
        sys.exit(1)

    # 6. Dry-run test from the unzipped folder
    print("[*] Running launch-and-wait verification from the unzipped directory...")
    unzipped_exe_path = os.path.join(unzipped_root, "AI_Video_Tool.exe")
    try:
        proc = subprocess.Popen(
            [unzipped_exe_path], 
            cwd=unzipped_root, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        try:
            # Wait for 5 seconds to verify if it crashes immediately
            exit_code = proc.wait(timeout=5)
            print(f"[!] EXE exited early with code {exit_code}")
            stdout, stderr = proc.communicate()
            print(f"[!] Stdout: {stdout}")
            print(f"[!] Stderr: {stderr}")
            if exit_code != 0 and "License" not in stdout and "License" not in stderr:
                print("[!] Launch dry-run failed (crash).")
                remove_dir(test_extract_dir)
                sys.exit(1)
            else:
                print("[+] EXE ran and exited cleanly.")
        except subprocess.TimeoutExpired:
            # If it's still running after 5 seconds, it's successful (it means GUI dialog is open)
            print("[+] EXE successfully launched from unzipped folder and remained running (no DLL crashes).")
            proc.terminate()
            proc.wait()
    except Exception as e:
        print(f"[!] Failed to run unzipped EXE: {e}")
        remove_dir(test_extract_dir)
        sys.exit(1)

    # Clean up test extraction folder
    remove_dir(test_extract_dir)
    print("[+] All verification checks PASSED successfully.")

if __name__ == "__main__":
    build_onedir()
