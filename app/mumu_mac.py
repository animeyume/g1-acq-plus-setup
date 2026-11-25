from pathlib import Path
from email.message import Message
import json,os,plistlib,re,shutil,subprocess,time
from urllib.parse import urlsplit,urlunsplit
from support import Adb,objects,truth,decode_json as json_output
REFERRAL="https://aff.gearupglobal.com/product/download/t65aHKpDb4em"
TEAM="9699UND7H5"

def command(args, timeout=60, check=True):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed")[-800:])
    return result

def installed_app():
    for parent in (Path("/Applications"), Path.home() / "Applications"):
        for name in ("MuMuPlayer Pro.app", "MuMuPlayer.app"):
            app = parent / name
            if (app / "Contents/MacOS/mumutool").is_file():
                return app
    return None

def mac_referral_url(headers):
    locations = re.findall(r"(?im)^location:\s*(\S+)\s*$", headers)
    if not locations:
        raise RuntimeError("The affiliate link did not return a download location.")
    parsed = urlsplit(locations[-1])
    if parsed.scheme != "https" or parsed.hostname != "api.mumuglobal.com" or parsed.path not in ("/api/dl/win", "/api/dl/mac"):
        raise RuntimeError("The affiliate redirect format changed. Refusing to guess a campaign.")
    # Keep the exact query returned by the affiliate service; change the vendor's platform route only.
    return urlunsplit((parsed.scheme, parsed.netloc, "/api/dl/mac", parsed.query, ""))

def response_filename(headers):
    values = re.findall(r"(?im)^content-disposition:\s*(.+?)\s*$", headers)
    if not values:
        raise RuntimeError("No affiliate filename was returned by MuMu.")
    message = Message()
    message["Content-Disposition"] = values[-1]
    name = message.get_filename()
    if not name or Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9_.()-]+\.dmg", name):
        raise RuntimeError("Unexpected MuMu download filename.")
    return name

def install_mumu(work):
    if shutil.disk_usage(work).free < 8 * 1024**3:
        raise RuntimeError("Free at least 8 GiB before installing MuMu and its Android device.")
    folder = work / "downloads"
    folder.mkdir(exist_ok=True)
    redirect = command(["/usr/bin/curl", "-fsSI", "--max-time", "30", REFERRAL]).stdout
    url = mac_referral_url(redirect)
    part, headers = folder / "mumu-download.part", folder / "mumu-headers.txt"
    print("Downloading MuMu for Mac through the affiliate campaign...", flush=True)
    try:
        subprocess.run(["/usr/bin/curl", "-fL", "--retry", "2", "--proto", "=https",
                 "--proto-redir", "=https", "--max-time", "1800",
                 "-D", str(headers), "-o", str(part), url], timeout=1830, check=True)
        filename = response_filename(headers.read_text(errors="replace"))
        if "_aff_" not in filename:
            raise RuntimeError("MuMu returned a download without the expected affiliate marker.")
        dmg = folder / filename
        part.replace(dmg)
    finally:
        part.unlink(missing_ok=True)
    result = subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)],
                            capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Could not mount the MuMu DMG: " + result.stderr.decode(errors="replace"))
    mounted = plistlib.loads(result.stdout)
    points = [Path(x["mount-point"]) for x in mounted["system-entities"] if "mount-point" in x]
    try:
        candidates = [p for mount in points for p in mount.glob("*.app")
                      if (p / "Contents/MacOS/mumutool").is_file()]
        if len(candidates) != 1:
            raise RuntimeError("The MuMu DMG layout changed; install MuMu manually and run again.")
        source = candidates[0]
        signature = command(["/usr/bin/codesign", "-dv", "--verbose=4", source])
        if "TeamIdentifier=" + TEAM not in signature.stderr:
            raise RuntimeError("The MuMu app publisher changed; installation stopped.")
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", source])
        command(["/usr/sbin/spctl", "--assess", "--type", "execute", source])
        destination = Path.home() / "Applications" / source.name
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            raise RuntimeError("Destination already exists: " + str(destination))
        command(["/usr/bin/ditto", source, destination], timeout=180)
        return destination
    finally:
        for mount in points:
            command(["/usr/bin/hdiutil", "detach", mount], check=False)

def device_rows(data):
    rows = {}
    for node in objects(data):
        for key in ("index", "deviceIndex", "device_index", "id"):
            index = node.get(key)
            if str(index).isdigit() and any(k in node for k in
                    ("name", "vmName", "status", "setting", "settings", "adbPort", "adb_port", "UUID", "uuid")):
                rows[int(index)] = dict(node)
        for key, child in node.items():
            if str(key).isdigit() and isinstance(child, dict) and any(k in child for k in
                    ("name", "vmName", "status", "setting", "settings", "adbPort", "adb_port", "UUID", "uuid")):
                rows[int(key)] = dict(child)
    return rows

def adb_port(row):
    # Actual port fields take priority over a configured custom port.
    for key in ("currentUsingAdbPort", "adbPort", "adb_port", "ADBPort", "adbPortNumber"):
        for node in objects(row):
            value = node.get(key)
            if value is not None and str(value).isdigit() and 0 < int(value) < 65536:
                return int(value)
    for node in objects(row):
        value = node.get("customAdbPort")
        if value and str(value).isdigit() and not truth(node.get("usingNormalADBPort", True)):
            return int(value)
    return None

class MuMu:
    def __init__(self, app):
        self.app = app
        self.tool = app / "Contents/MacOS/mumutool"

    def call(self, *args):
        result = command([self.tool, *args])
        return result.stdout.strip()

    def rows(self):
        data = json_output(self.call("info", "all"))
        rows = device_rows(data)
        if not rows and data not in ({}, []) and not (isinstance(data, dict) and data.get("devices") == []):
            raise RuntimeError("Unsupported MuMu info schema. No device will be created or modified.")
        return rows

def choose(rows, requested=None):
    if requested is not None:
        if requested not in rows:
            raise RuntimeError("MuMu device not found: " + str(requested))
        return requested
    if len(rows) == 1:
        return next(iter(rows))
    print("Select one MuMu Android device:")
    for key, value in sorted(rows.items()):
        print(f"  {key}: {value.get('name', value.get('vmName', 'Android'))}")
    while True:
        text = input("Device number: ").strip()
        if text.isdigit() and int(text) in rows:
            return int(text)

def find_adb(app):
    options = [
        app / "Contents/MacOS/MuMu Android Device.app/Contents/MacOS/tools/adb",
        app / "Contents/MacOS/adb",
    ]
    for candidate in options:
        if candidate.is_file():
            return candidate
    for candidate in (app / "Contents").rglob("adb"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("MuMu's bundled adb could not be found.")

def wait_rows(mumu):
    while True:
        try:
            return mumu.rows()
        except RuntimeError as error:
            if "Unsupported MuMu info schema" in str(error):
                raise
            print("Open MuMu and finish its initial setup/sign-in if it asks.")
            if input("Press Enter when MuMu is ready, or Q to stop: ").strip().lower() == "q":
                raise RuntimeError("Setup stopped by user.")

def connect_device(mumu, index, exe, allow_prompt=True):
    deadline = time.monotonic() + 180
    entered = None
    while time.monotonic() < deadline:
        rows = mumu.rows()
        if index not in rows:
            raise RuntimeError("The selected MuMu device disappeared.")
        port = adb_port(rows[index]) or entered
        if port:
            adb = Adb(exe, f"127.0.0.1:{port}")
            try:
                adb.connect()
                return adb
            except (RuntimeError, subprocess.SubprocessError):
                pass
        elif allow_prompt:
            value = input("Open Developer > Open ADB in this MuMu device. Enter its local ADB port: ").strip()
            if not value.isdigit() or not 1 <= int(value) <= 65535:
                raise RuntimeError("Invalid ADB port.")
            entered = int(value)
        time.sleep(3)
    raise RuntimeError("MuMu Android did not become ready. Enable local ADB, then run again.")
