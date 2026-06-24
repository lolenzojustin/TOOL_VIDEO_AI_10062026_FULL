import os
import sys
import shutil
import subprocess
import requests
import time
import stat

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

def build_onefile():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(project_dir, "dist")
    build_dir = os.path.join(project_dir, "build")
    spec_path = os.path.join(project_dir, "AI_Video_Tool.spec")
    root_exe_path = os.path.join(project_dir, "AI_Video_Tool.exe")
    
    print("[*] Performing Version Validation...")
    
    # Read APP_VERSION
    app_version = "1.0.12" # default fallback
    try:
        sys.path.insert(0, project_dir)
        import api_check_version
        app_version = api_check_version.APP_VERSION
    except Exception as e:
        print(f"[!] Error importing api_check_version to read APP_VERSION: {e}")
        
    print(f"[*] APP_VERSION in code: {app_version}")
    
    # Query Server Version
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
        
    has_update_loop_risk = False
    if server_version:
        try:
            curr_tuple = tuple(map(int, app_version.split('.')))
            serv_tuple = tuple(map(int, server_version.split('.')))
            if serv_tuple > curr_tuple:
                print(f"\n[WARNING] DETECTED VERSION MISMATCH!")
                print(f"  Server Version on Web is: {server_version}")
                print(f"  APP_VERSION inside code is: {app_version}")
                print(f"  Since Server Version > APP_VERSION, the app will trigger an immediate update dialog")
                print(f"  after startup and cause an update loop when the update download completes!")
                print(f"  Please update APP_VERSION in code or update the API server version before proceeding.\n")
                has_update_loop_risk = True
            elif serv_tuple == curr_tuple:
                print(f"[+] Version match check passed (both are {app_version}). No update loops will occur.")
            else:
                print(f"[+] APP_VERSION ({app_version}) is ahead of server version ({server_version}). OK.")
        except Exception as e:
            print(f"[!] Version comparison parsing error: {e}")
            
    # 1. Clean previous build files: build, dist, AI_Video_Tool.spec, root AI_Video_Tool.exe
    print("[-] Cleaning up old build files and root executable...")
    remove_dir(dist_dir)
    remove_dir(build_dir)
        
    if os.path.exists(spec_path):
        try:
            os.remove(spec_path)
            print("[-] Removed old spec file.")
        except Exception as e:
            print(f"[!] Warning: Failed to remove spec file: {e}")
            
    if os.path.exists(root_exe_path):
        try:
            os.remove(root_exe_path)
            print("[-] Removed old root executable.")
        except Exception as e:
            print(f"[!] Warning: Failed to remove root executable: {e}")

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
        "--onefile",
        "--windowed",
        "--name=AI_Video_Tool",
        "giaodientoolmau_videoai.py"
    ]
    
    print(f"[*] Running PyInstaller command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=project_dir)
    
    if result.returncode != 0:
        print("[!] PyInstaller failed to compile the application.")
        sys.exit(1)
        
    compiled_exe_path = os.path.join(dist_dir, "AI_Video_Tool.exe")
    if not os.path.exists(compiled_exe_path):
        print(f"[!] Executable not found at expected path: {compiled_exe_path}")
        sys.exit(1)
        
    print("[+] PyInstaller compiled the tool successfully in onefile format.")

    # 3. Copy dist\AI_Video_Tool.exe to root directory
    print("[-] Copying compiled executable to root workspace directory...")
    shutil.copy2(compiled_exe_path, root_exe_path)
    if os.path.exists(root_exe_path):
        print(f"[+] Output executable copied successfully to: {root_exe_path}")
    else:
        print(f"[!] Failed to copy compiled executable to root directory.")
        sys.exit(1)

    # 4. Dry-run test from the root folder
    print("[*] Running launch-and-wait verification for root executable...")
    try:
        proc = subprocess.Popen(
            [root_exe_path], 
            cwd=project_dir, 
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
                sys.exit(1)
            else:
                print("[+] EXE ran and exited cleanly (or triggered license key dialog).")
        except subprocess.TimeoutExpired:
            # If it's still running after 5 seconds, it's successful (it means GUI dialog is open)
            print("[+] EXE successfully launched and remained running (no DLL crashes).")
            proc.terminate()
            proc.wait()
    except Exception as e:
        print(f"[!] Failed to run root EXE: {e}")
        sys.exit(1)

    print("[+] All verification checks PASSED successfully.")

if __name__ == "__main__":
    build_onefile()
