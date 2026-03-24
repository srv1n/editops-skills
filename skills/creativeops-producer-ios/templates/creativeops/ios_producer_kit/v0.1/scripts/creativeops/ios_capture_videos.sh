#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ios_capture_videos.sh --project <xcodeproj|xcworkspace> --scheme <UITestScheme> --destination '<dest>' \
    --flow-id <flow_id> --plan-path <video_plan.json> --run-group <group> [--locale en_US] [--language en]

This script is a minimal “producer” capture harness:
- Runs an XCUITest that waits for READY and writes a points-space events JSON.
- Starts `simctl recordVideo` when READY is seen.
- Writes GO so the UI test can treat it as t=0.
- Stops recording when the UI test writes STOP, then writes STOPPED ack.
- Converts points-space events → pixel-space ios_ui_events.json.

You must implement the UI test in your repo to match the marker filenames and output path.
EOF
}

PROJECT=""
SCHEME=""
DESTINATION=""
FLOW_ID=""
PLAN_PATH=""
RUN_GROUP=""
LOCALE="en_US"
LANGUAGE="en"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --scheme) SCHEME="$2"; shift 2 ;;
    --destination) DESTINATION="$2"; shift 2 ;;
    --flow-id) FLOW_ID="$2"; shift 2 ;;
    --plan-path) PLAN_PATH="$2"; shift 2 ;;
    --run-group) RUN_GROUP="$2"; shift 2 ;;
    --locale) LOCALE="$2"; shift 2 ;;
    --language) LANGUAGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

if [[ -z "$PROJECT" || -z "$SCHEME" || -z "$DESTINATION" || -z "$FLOW_ID" || -z "$PLAN_PATH" || -z "$RUN_GROUP" ]]; then
  usage
  exit 2
fi

ROOT_DIR="$(pwd)"

extract_destination_value() {
  local key="${1}"
  local dest="${2}"

  local part
  IFS=',' read -ra parts <<< "${dest}"
  for part in "${parts[@]}"; do
    if [[ "${part}" == "${key}="* ]]; then
      printf "%s" "${part#${key}=}"
      return 0
    fi
  done
  return 1
}

DEVICE_NAME="$(extract_destination_value "name" "${DESTINATION}" || true)"
DEVICE_NAME="${DEVICE_NAME:-iPhone 16}"

RUN_DIR="${ROOT_DIR}/creativeops/runs/${RUN_GROUP}/${LOCALE}/${DEVICE_NAME}/${FLOW_ID}"
mkdir -p "${RUN_DIR}/inputs" "${RUN_DIR}/signals" "${RUN_DIR}/producer"
cp -f "${PLAN_PATH}" "${RUN_DIR}/producer/video_plan.json" || true
if [[ -f "${ROOT_DIR}/creativeops/producer/ios/id_registry.yaml" ]]; then
  cp -f "${ROOT_DIR}/creativeops/producer/ios/id_registry.yaml" "${RUN_DIR}/producer/id_registry.yaml" || true
fi

SIM_UDID="$(extract_destination_value "id" "${DESTINATION}" || true)"
if [[ -z "${SIM_UDID}" ]]; then
  SIM_UDID="$(python3 - <<'PY' "${DEVICE_NAME}"
import json, subprocess, sys, re
name = sys.argv[1]
raw = subprocess.check_output(["xcrun", "simctl", "list", "devices", "-j"], text=True)
data = json.loads(raw)
def rv(rt):
  m=re.search(r"iOS-(.+)$", rt)
  if not m: return (0,)
  return tuple(int(x) if x.isdigit() else 0 for x in m.group(1).split("-"))
c=[]
for rt, ds in (data.get("devices") or {}).items():
  for d in ds or []:
    if d.get("name")==name and d.get("isAvailable", True):
      c.append((rt,d))
if not c: raise SystemExit(1)
boot=[x for x in c if x[1].get("state")=="Booted"]
pool=boot if boot else c
pool.sort(key=lambda x: rv(x[0]), reverse=True)
print(pool[0][1]["udid"])
PY
)"
fi

echo "Simulator UDID: ${SIM_UDID}"
echo "Run dir: ${RUN_DIR}"

# ── Suppress simulator education/tips popups ──
echo "Disabling simulator tips and first-run dialogs..."
xcrun simctl spawn "${SIM_UDID}" defaults write -g com.apple.keyboard.HWKeyboardEnabled -bool YES 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write com.apple.tips TipsFirstLaunchComplete -bool YES 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write com.apple.tips ShowTips -bool NO 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write com.apple.Preferences FBShowTipsInSettings -bool NO 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write com.apple.springboard SBDidShowRTLBanner -bool YES 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write com.apple.springboard SBDontLockAfterCrash -bool YES 2>/dev/null || true
xcrun simctl spawn "${SIM_UDID}" defaults write -g UIKeyboardDidShowInternationalInfoIntroduction -bool YES 2>/dev/null || true

MARKER_DIR="${PRODUCER_CACHE_DIR:-${HOME}/Library/Caches/CreativeOpsProducer}"
mkdir -p "${MARKER_DIR}"
READY="${MARKER_DIR}/video_recording_ready.txt"
GO="${MARKER_DIR}/video_recording_go.txt"
STOP="${MARKER_DIR}/video_recording_stop.txt"
STOPPED="${MARKER_DIR}/video_recording_stopped.txt"
POINTS="${MARKER_DIR}/video_ui_events_points.json"

rm -f "${READY}" "${GO}" "${STOP}" "${STOPPED}" "${POINTS}"

TMP_MP4="${RUN_DIR}/inputs/input.mp4"

cleanup() {
  true
}
trap cleanup EXIT

echo "Starting XCUITest in background..."
set +e
xcodebuild -project "${PROJECT}" -scheme "${SCHEME}" -destination "${DESTINATION}" test \
  VIDEO_FLOW_ID="${FLOW_ID}" \
  VIDEO_PLAN_PATH="${PLAN_PATH}" \
  VIDEO_APPLE_LOCALE="${LOCALE}" \
  VIDEO_APPLE_LANGUAGE="${LANGUAGE}" \
  SIMULATOR_HOST_HOME="${HOME}" \
  >"${RUN_DIR}/producer/xcodebuild.log" 2>&1 &
XCB_PID=$!
set -e

echo "Waiting for READY marker..."
python3 - <<'PY' "${READY}"
import sys, time
p=sys.argv[1]
deadline=time.time()+60
while time.time()<deadline:
  if __import__("os").path.exists(p):
    sys.exit(0)
  time.sleep(0.05)
print("Timed out waiting for READY", file=sys.stderr)
sys.exit(2)
PY

echo "Starting simctl recordVideo..."
REC_LOG="${RUN_DIR}/producer/recording.log"
rm -f "${REC_LOG}" >/dev/null 2>&1 || true
xcrun simctl io "${SIM_UDID}" recordVideo --codec=h264 --force --mask ignored "${TMP_MP4}" >/dev/null 2>"${REC_LOG}" &
REC_PID=$!

echo "Waiting for recorder to start..."
python3 - <<'PY' "${REC_LOG}"
import sys, time, os
p=sys.argv[1]
deadline=time.time()+10
while time.time()<deadline:
  if os.path.exists(p):
    try:
      txt=open(p,"r",encoding="utf-8",errors="ignore").read()
      if "Recording started" in txt:
        sys.exit(0)
    except Exception:
      pass
  time.sleep(0.05)
sys.exit(0)
PY

date +%s > "${GO}"

echo "Waiting for STOP marker..."
python3 - <<'PY' "${STOP}"
import sys, time, os
p=sys.argv[1]
deadline=time.time()+180
while time.time()<deadline:
  if os.path.exists(p):
    sys.exit(0)
  time.sleep(0.05)
print("Timed out waiting for STOP", file=sys.stderr)
sys.exit(2)
PY

echo "Stopping recorder..."
kill -INT "${REC_PID}" >/dev/null 2>&1 || true
wait "${REC_PID}" >/dev/null 2>&1 || true
date +%s > "${STOPPED}"

echo "Waiting for xcodebuild..."
wait "${XCB_PID}" || true

if [[ ! -f "${POINTS}" ]]; then
  echo "❌ Missing points events JSON: ${POINTS}"
  exit 2
fi

echo "Converting points → pixels..."
VID_W="$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=nk=1:nw=1 "${TMP_MP4}")"
VID_H="$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=nk=1:nw=1 "${TMP_MP4}")"
python3 scripts/creativeops/ios_ui_events_points_to_pixels.py "${POINTS}" "${VID_W}" "${VID_H}" "${RUN_DIR}/signals/ios_ui_events.json"

# Optional but recommended: provide a camera-only focus stream so camera_follow doesn't react to tap_target rects.
python3 - <<'PY' "${RUN_DIR}/signals/ios_ui_events.json" "${RUN_DIR}/signals/ios_camera_focus.json"
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
data = json.loads(src.read_text(encoding="utf-8"))
focus = data.get("focus") or []
filtered = [f for f in focus if str(f.get("kind") or "") in ("camera", "screen")]
out = dict(data)
if not filtered:
  v = data.get("video") or {}
  w = int(v.get("width") or 1)
  h = int(v.get("height") or 1)
  filtered = [{"t_ms": 0, "rect": {"x": 0, "y": 0, "w": w, "h": h}, "id": "screen", "kind": "screen", "confidence": 1.0}]
out["focus"] = filtered
out["events"] = []
out["elements"] = out.get("elements") or {}
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "✅ Capture complete: ${RUN_DIR}"
