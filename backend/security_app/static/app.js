"use strict";
const $ = id => document.getElementById(id);
let viewEpoch = 0;
let peopleById = new Map();
let csrf = sessionStorage.getItem("sss_csrf") || "",
  current = null,
  events = [],
  busy = false,
  imageUrl = null;

// Metrics tracking
let lastFrameId = null;
let frameCount = 0;
let fpsStartTime = performance.now();
let currentFps = 0;
let lastRtt = null;
let activeTab = "monitor", cameraStatusValid = false, cameraTarget = null;
let displayedFrame = null, displayedMeta = null;
let nextListsRefresh = 0, cachedPeople = [], lastStatusRefresh = -Infinity;
let lastCompletedVisit = null;

const labels = {
  KNOWN: "Người quen",
  UNKNOWN: "Không khớp người đã đăng ký",
  UNCERTAIN: "Chưa xác định",
  NO_FACE: "Chưa thấy mặt",
  NOT_CHECKED: "Chưa ở vùng nhận diện",
  ARMED: "Đang bật giám sát",
  DISARMED: "Đã tắt giám sát",
  ENROLLMENT: "Đang đăng ký",
  INCOMING: "Từ vùng ngoài đến cửa",
  OUTGOING: "Từ cửa ra ngoài",
  UNRESOLVED: "Chưa rõ chiều đi",
  known_visit: "Người quen ở vùng cửa",
  visit_completed: "Kết quả lượt vừa kết thúc",
  notification_test: "Thử thông báo Telegram",
  unknown_approach: "Người chưa đăng ký tiếp cận cửa",
  unresolved_presence: "Cần kiểm tra người ở vùng cửa",
  capacity_exceeded: "Vượt phạm vi hai người",
  acked: "Thiết bị đã xác nhận",
  pending: "Đang chờ",
  superseded: "Đã thay bằng lệnh mới",
  expired: "Hết hạn",
  cancelled: "Đã hủy",
  rejected: "Thiết bị từ chối",
  disabled: "Đã tắt",
  sent: "Telegram đã nhận",
  sending: "Đang gửi",
  failed: "Gửi thất bại",
  unknown: "Chưa xác nhận gửi thành công"
};

const setupMessages = {
  DISABLED: "Telegram đang tắt.",
  PROVIDER_NOT_CONFIGURED: "Chưa cấu hình bot Telegram và người nhận trên server.",
  BOT_TOKEN_INVALID: "Token bot chưa đúng định dạng; cần kiểm tra cấu hình server.",
  CHAT_ID_INVALID: "Người nhận Telegram chưa được cấu hình đúng.",
  BOT_TOKEN_REJECTED: "Telegram từ chối token bot; cần cấu hình lại.",
  BOT_BLOCKED_OR_FORBIDDEN: "Bot bị chặn hoặc chưa được phép gửi tới người nhận.",
  PHOTO_CONSENT_REQUIRED: "Chưa bật quyền gửi ảnh camera qua Telegram.",
  NOTIFICATION_TEST_RATE_LIMIT: "Vui lòng chờ 30 giây trước khi gửi thử tiếp.",
  DELIVERY_UNCONFIRMED: "Chưa xác nhận lần gửi; gửi lại có thể trùng thông báo.",
  PROCESS_INTERRUPTED: "Server dừng trong khi gửi; chưa xác định Telegram đã nhận hay chưa.",
  DELIVERY_STATE_LOST: "Chưa xác định kết quả gửi sau lỗi lưu trạng thái.",
  RATE_LIMIT: "Telegram giới hạn tốc độ; hệ thống đang chờ đến lượt gửi lại.",
  PROVIDER_ERROR: "Telegram tạm lỗi; hệ thống sẽ gửi lại trong thời hạn.",
  PROVIDER_REJECTED: "Telegram từ chối yêu cầu; kiểm tra bot và người nhận.",
  PROVIDER_RESPONSE_INVALID: "Phản hồi Telegram không đủ để xác nhận gửi thành công.",
  PHOTO_REJECTED_TEXT_SENT: "Ảnh bị từ chối; nội dung thông báo đã được gửi.",
  ALERT_TOO_OLD: "Thông báo quá hạn, không gửi lại cảnh báo cũ.",
  PERSON_DELETED: "Đã hủy thông báo chưa gửi sau khi xóa hồ sơ.",
  PERSON_DELETED_DURING_DELIVERY: "Hồ sơ bị xóa khi đang gửi; cần kiểm tra kết quả trên Telegram.",
  FRAME_NOT_AVAILABLE: "Chưa có ảnh camera mới; mở video rồi thử lại.",
  CAMERA_PROFILE_CHANGED: "Luồng xử lý hoặc bố trí camera đã thay đổi. Cần kiểm chứng và duyệt lại hồ sơ hiệu chỉnh trước khi bật giám sát. Video và đăng ký mẫu vẫn dùng được.",
  CALIBRATION_REQUIRED: "Chưa có hồ sơ hiệu chỉnh được duyệt. Hãy hoàn tất đăng ký mẫu và kiểm chứng camera trước khi bật giám sát.",
  EMPTY_GALLERY: "Chưa có người quen đang được dùng để nhận diện. Vào Người quen để đăng ký, lưu mẫu và kích hoạt hồ sơ sau khi kiểm chứng.",
  GALLERY_SAMPLES_REQUIRED: "Hồ sơ chưa đủ mẫu hợp lệ. Hãy hoàn tất hai lượt đăng ký và lưu mẫu.",
  GALLERY_REENROLL_REQUIRED: "Mẫu đã lưu không tương thích với model hiện tại. Cần đăng ký lại trước khi giám sát."
};

function setupMessage(code) {
  return setupMessages[code] || code;
}

const recognitionMessages = {
  MATCH_SCORE_AMBIGUOUS: "Điểm nhận diện chưa đủ rõ để kết luận người quen hay người chưa đăng ký.",
  MATCH_MARGIN_AMBIGUOUS: "Khuôn mặt giống nhiều hồ sơ; chưa phân biệt chắc chắn.",
  INSUFFICIENT_IDENTITY_EVIDENCE: "Chưa đủ ảnh mặt hợp lệ để xác nhận danh tính.",
  IDENTITY_EVIDENCE_EXPIRED: "Bằng chứng nhận diện đã cũ; cần ảnh mặt mới.",
  IDENTITY_EVIDENCE_CONFLICT: "Các ảnh cho kết quả người quen và người chưa đăng ký mâu thuẫn.",
  IDENTITY_CONFLICT: "Danh tính trong lượt bị mâu thuẫn; cần kiểm tra lại."
};

function appendRecognitionDetails(row, result) {
  const code = result.identity_reason;
  const message = recognitionMessages[code] || enrollmentFeedback[code] || setupMessages[code];
  if (message) row.append(node("p", message));
  const counts = result.identity_evidence?.reason_counts || {};
  const quality = ["FACE_BLURRED", "FACE_TOO_SMALL", "FACE_EXPOSURE", "NO_FACE", "SCENE_TOO_DARK", "SCENE_EXPOSURE_FAILURE", "FACE_ASSOCIATION_AMBIGUOUS"];
  const reasons = quality.filter(key => counts[key] > 0).map(key => `${labels[key] || enrollmentFeedback[key] || "Chưa thấy mặt"} (${counts[key]} ảnh)`);
  if (reasons.length) row.append(node("p", `Trong lượt: ${reasons.join(" · ")}`));
  if (result.geometry_valid === false) {
    row.append(node("p", "Chưa ghép chắc chắn mặt với thân người; chưa đủ điều kiện xác định chiều tiếp cận để báo động."));
  } else if (result.direction === "UNRESOLVED") {
    row.append(node("p", "Chưa xác nhận đường đi từ vùng A đến vùng B để báo động."));
  }
}

const enrollmentFeedback = {
  "Face the camera; follow the enrollment guide": "Đưa mặt vào khung bầu dục, nhìn vào camera để bắt đầu thu mẫu.",
  "Round 2: change distance/angle slightly; keep only one person": "Lượt 2: chỉ một người trước camera; đổi nhẹ góc hoặc khoảng cách.",
  ACCEPTED: "Đã nhận một mẫu đạt chất lượng. Giữ chuyển động chậm, đổi nhẹ góc mặt.",
  WAIT_BETWEEN_SAMPLES: "Giữ yên một chút trước mẫu tiếp theo.",
  SAMPLE_TOO_SIMILAR_CHANGE_POSE: "Ảnh gần giống mẫu đã có. Quay nhẹ trái/phải hoặc đổi nhẹ khoảng cách.",
  SAMPLE_IDENTITY_CONFLICT_RESTART_OR_REVIEW: "Mẫu không khớp các mẫu cùng phiên. Kiểm tra đúng người; hủy và đăng ký lại nếu cần.",
  POSSIBLE_EXISTING_PERSON_REVIEW: "Mẫu giống một người đã đăng ký. Kiểm tra hồ sơ trước khi tiếp tục.",
  ROUND_COMPLETE_USE_NEXT_ROUND_OR_COMMIT: "Lượt này đã đủ mẫu. Làm theo bước tiếp theo bên trên.",
  EXACTLY_ONE_PERSON_AND_FACE_REQUIRED: "Cần đúng một khuôn mặt và chỉ một người trước camera.",
  EXACTLY_ONE_FACE_REQUIRED: "Chỉ để một khuôn mặt xuất hiện trong ảnh.",
  FACE_OUTSIDE_GUIDE: "Đưa mặt vào giữa khung bầu dục.",
  ENROLLMENT_REQUIRES_ZONE_B: "Di chuyển vào vùng B trên ảnh; camera cần xác định bạn ở vùng đăng ký.",
  FACE_ASSOCIATION_AMBIGUOUS: "Chưa ghép chắc chắn mặt với người. Đứng thẳng, không để người khác lọt vào ảnh.",
  FACE_TOO_SMALL: "Mặt còn nhỏ. Tiến lại gần một chút, giữ trong khung hướng dẫn.",
  FACE_BLURRED: "Mặt bị mờ. Giữ yên, quay chậm và kiểm tra ánh sáng.",
  FACE_EXPOSURE: "Mặt quá tối hoặc quá sáng. Tránh ngược sáng, chiếu sáng đều phía trước mặt.",
  SCENE_TOO_DARK: "Khung cảnh quá tối. Tăng ánh sáng trước khi thu tiếp.",
  SCENE_EXPOSURE_FAILURE: "Ảnh bị tối hoặc cháy sáng nhiều. Đổi hướng camera hoặc ánh sáng.",
  LANDMARKS_INVALID: "Chưa thấy rõ các nét mặt. Nhìn gần chính diện, bỏ vật che mặt.",
  FACE_CROP_EMPTY: "Chưa lấy được vùng mặt hợp lệ. Đưa mặt trọn vào ảnh."
};

function renderEnrollment() {
  const capture = current.capture;
  const active = capture.active && capture.purpose === "enrollment";
  $("enrollment").hidden = !active;
  $("enrollment-guide").hidden = active;
  $("enrollment-setup").hidden = active;
  $("people-list").hidden = active;
  const required = current.enrollment_requirements.per_round_min;
  for (const span of document.querySelectorAll("[data-round-min]")) span.textContent = required;
  if (!active) return;

  const total = capture.accepted.reduce((sum, count) => sum + count, 0);
  const roundCount = capture.accepted[capture.round - 1];
  const complete = Math.min(...capture.accepted) >= required && total >= current.enrollment_requirements.accepted_min;
  $("enrollment-title").textContent = `Đăng ký khuôn mặt · ${peopleById.get(capture.person_id) || "Hồ sơ đang chọn"}`;
  const seconds = Math.max(0, Math.ceil(capture.remaining_ms / 1000));
  $("enrollment-time").textContent = `Còn ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  $("enrollment-progress").textContent = `${total}/${current.enrollment_requirements.accepted_min} mẫu đạt · ${capture.rejected} ảnh bị loại`;
  $("enrollment-meter").max = current.enrollment_requirements.accepted_min;
  $("enrollment-meter").value = total;
  $("enrollment-rounds").textContent = `Lượt 1: ${capture.accepted[0]}/${required} · Lượt 2: ${capture.accepted[1]}/${required}`;
  $("enrollment-feedback").textContent = enrollmentFeedback[capture.last_feedback] || capture.last_feedback || "Đang chờ kết quả thu mẫu.";

  const poses = ["Nhìn thẳng vào camera", "Quay mặt nhẹ sang trái", "Quay mặt nhẹ sang phải", "Trở lại nhìn thẳng", "Đổi nhẹ góc hoặc khoảng cách"];
  const poseIndex = Math.min(poses.length - 1, Math.floor(roundCount * poses.length / required));
  if (complete) {
    $("enrollment-pose").textContent = "Đã đủ mẫu cho hai lượt";
    $("enrollment-next-step").textContent = "Bấm Lưu mẫu để hoàn tất. Hồ sơ sẽ ở trạng thái nháp, chưa tự kích hoạt giám sát.";
  } else if (capture.round === 1 && roundCount >= required) {
    $("enrollment-pose").textContent = "Hoàn tất lượt 1";
    $("enrollment-next-step").textContent = "Nghỉ ngắn, trở lại khung hình với góc hoặc khoảng cách hơi khác, rồi bấm Bắt đầu lượt 2.";
  } else {
    $("enrollment-pose").textContent = poses[poseIndex];
    $("enrollment-next-step").textContent = `Lượt ${capture.round}/2 · còn ${Math.max(0, required - roundCount)} mẫu. Giữ mặt rõ trong khung hướng dẫn, không quay ngang hoàn toàn. Trái/phải là phía của bạn.`;
  }
  $("enrollment-camera-pose").textContent = $("enrollment-pose").textContent;
  const noImage = current.frame_age_ms === null;
  const staleImage = !noImage && current.frame_age_ms > 3000;
  const cameraState = $("enrollment-camera-state");
  if (!current.device_online) cameraState.textContent = "Thiết bị offline. Kiểm tra nguồn và Wi-Fi; chưa thể nhận mẫu mới.";
  else if (noImage || staleImage) cameraState.textContent = "Chưa có ảnh mới từ camera. Số mẫu chưa thể tăng; kiểm tra nguồn, kết nối và đường gửi ảnh.";
  else cameraState.textContent = "Video và tiến độ lấy mẫu cập nhật độc lập. Đưa mặt vào khung bầu dục.";
  cameraState.classList.toggle("enrollment-camera-warning", !current.device_online || noImage || staleImage);
  $("next-round").disabled = capture.round !== 1 || capture.accepted[0] < required;
  $("commit-enrollment").disabled = !complete;
  drawEnrollmentGuide();
}

function drawEnrollmentGuide() {
  const canvas = $("enrollment-overlay"), img = $("enrollment-image");
  if (!img.complete || !img.naturalWidth || img.hidden) return;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!current?.capture.active || current.capture.purpose !== "enrollment") return;
  const zone = current.enrollment_requirements?.face_region;
  if (!zone) return;
  const [x, y, w, h] = zone;
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  const cx = (x + w / 2) * canvas.width, cy = (y + h / 2) * canvas.height;
  const rx = w * canvas.width / 2, ry = h * canvas.height / 2;
  ctx.setLineDash([7, 6]);
  ctx.beginPath();
  ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  const accepted = current.capture.accepted.reduce((sum, count) => sum + count, 0);
  const fraction = Math.min(1, accepted / current.enrollment_requirements.accepted_min);
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.ellipse(cx, cy, rx, ry, 0, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * fraction);
  ctx.stroke();
}

function actionKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

function show(text) {
  $("message").textContent = text;
  $("message").classList.add("visible");
}

function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}

async function api(path, method = "GET", body) {
  const response = await fetch("/api/v2" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (response.status === 401 && path !== "/auth/login") {
    logoutView();
    throw new Error("Cần đăng nhập lại.");
  }
  const result = response.status === 204 ? {} : await response.json();
  if (!response.ok) throw new Error(setupMessage(result.error?.code) || "Yêu cầu thất bại");
  return result;
}

/* Lightbox Modal functions */
function openLightbox(mediaId, title, meta) {
  const dialog = $("lightbox-dialog");
  if (!dialog) return;
  const titleEl = $("lightbox-title");
  if (titleEl) titleEl.textContent = title || "Ảnh bằng chứng sự kiện";
  const metaEl = $("lightbox-meta");
  if (metaEl) metaEl.textContent = meta || "";
  const src = `/api/v2/media/${mediaId}`;
  const imgEl = $("lightbox-img");
  if (imgEl) imgEl.src = src;
  const dl = $("lightbox-download");
  if (dl) {
    dl.href = src;
    dl.download = `evidence-${mediaId}.jpg`;
  }
  dialog.showModal();
}

function closeLightbox() {
  const dialog = $("lightbox-dialog");
  if (dialog && dialog.open) {
    dialog.close();
    const imgEl = $("lightbox-img");
    if (imgEl) imgEl.removeAttribute("src");
  }
}

const lbClose = $("lightbox-close");
if (lbClose) lbClose.onclick = closeLightbox;
const lbDismiss = $("lightbox-dismiss");
if (lbDismiss) lbDismiss.onclick = closeLightbox;
const lbDialog = $("lightbox-dialog");
if (lbDialog) {
  lbDialog.addEventListener("click", e => {
    if (e.target === lbDialog) closeLightbox();
  });
}

const cameraStream = new CameraStream({
  onFrame: displayFrame,
  onClear: clearImages,
  getCsrf: () => csrf,
  getSettings: () => current?.video || {},
  onState: text => {
    $("live-state").textContent = text;
    if (cameraTarget === "enrollment-image") $("enrollment-camera-state").textContent = text;
  },
  onUnauthorized: logoutView
});

function stopCamera() {
  cameraTarget = null;
  if (cameraStream.key !== null || imageUrl) cameraStream.stop();
}

function syncCamera() {
  const capture = current?.capture;
  let target = null;
  if (csrf && cameraStatusValid && !document.hidden && current.device_online) {
    if (activeTab === "people" && capture?.active && capture.purpose === "enrollment") target = "enrollment-image";
    else if (activeTab === "monitor") target = "camera-image";
  }
  if (!target) { stopCamera(); return; }
  cameraTarget = target;
  cameraStream.start(target, target === "enrollment-image" ? capture.id : null);
  if (target === "camera-image") drawMonitorOverlay();
  else drawEnrollmentGuide();
}

function invalidateCamera() {
  viewEpoch++;
  cameraStatusValid = false;
  stopCamera();
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) invalidateCamera();
  else refresh();
});
window.addEventListener("pagehide", invalidateCamera);

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

function clearImages() {
  frameRenderer.clear();
  displayedMeta = null;
  for (const id of ["camera-image", "enrollment-image"]) {
    $(id).removeAttribute("src");
    $(id).hidden = true;
  }
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = null;
  displayedFrame = null;
  lastFrameId = null;
  frameCount = 0;
  fpsStartTime = performance.now();
  $("metric-fps").textContent = "FPS: --";
  const canvas = $("overlay");
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
  $("image-empty").hidden = false;
  $("enrollment-image-empty").hidden = false;
  const enrollmentCanvas = $("enrollment-overlay");
  enrollmentCanvas.getContext("2d").clearRect(0, 0, enrollmentCanvas.width, enrollmentCanvas.height);
  closeLightbox();
}

function logoutView() {
  invalidateCamera();
  current = null;
  events = [];
  cachedPeople = [];
  nextListsRefresh = 0;
  lastCompletedVisit = null;
  peopleById.clear();
  clearImages();
  for (const id of ["event-list", "people-list", "tracks"]) $(id).replaceChildren();
  csrf = "";
  sessionStorage.removeItem("sss_csrf");
  $("app").hidden = true;
  $("login-panel").hidden = false;
  $("logout").hidden = true;
  $("connection").textContent = "Chưa đăng nhập";
}

async function action(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (e) {
    show(e.message);
  } finally {
    button.disabled = false;
  }
  await refresh();
}

function cameraAction(button, fn) {
  viewEpoch++;
  nextListsRefresh = 0;
  return action(button, fn);
}

$("login-form").onsubmit = async e => {
  e.preventDefault();
  await action(e.submitter, async () => {
    const result = await api("/auth/login", "POST", {
      username: $("username").value,
      password: $("password").value
    });
    viewEpoch++;
    csrf = result.csrf_token;
    sessionStorage.setItem("sss_csrf", csrf);
    $("password").value = "";
    $("message").classList.remove("visible");
  });
};

$("logout").onclick = async e => cameraAction(e.target, async () => {
  await api("/auth/logout", "POST", {});
  logoutView();
});

for (const button of document.querySelectorAll("nav button")) button.onclick = () => {
  for (const b of document.querySelectorAll("nav button")) b.classList.toggle("selected", b === button);
  for (const tab of document.querySelectorAll(".tab")) tab.hidden = tab.id !== button.dataset.tab;
  activeTab = button.dataset.tab;
  syncCamera();
};

for (const [id, mode] of [
    ["arm", "ARMED"],
    ["disarm", "DISARMED"]
  ]) $(id).onclick = e => cameraAction(e.target, async () => {
  await api("/system/mode", "PUT", {
    mode,
    expected_revision: current.mode_revision
  });
  show(labels[mode]);
});

$("recognition-trace").onclick = e => action(e.target, async () => {
  await api("/diagnostics/recognition/start", "POST", {});
  show("Đang ghi số đo trong 2 phút. Bạn có thể thực hiện các lượt thử.");
});

$("telegram-test").onclick = e => action(e.target, async () => {
  await api("/notifications/test", "POST", {
    idempotency_key: actionKey(), include_photo: $("telegram-test-photo").checked
  });
  nextListsRefresh = 0;
  show("Đã xếp hàng thông báo thử. Kiểm tra Telegram trên điện thoại và trạng thái trong lịch sử.");
});

$("test").onclick = e => action(e.target, async () => {
  await api("/system/test-alarm", "POST", {
    idempotency_key: actionKey()
  });
  show("Đã tạo lệnh test; chờ thiết bị xác nhận.");
});

$("cancel-enrollment").onclick = e => cameraAction(e.target, () => api("/capture", "DELETE"));
$("next-round").onclick = e => cameraAction(e.target, () => api(`/enrollments/${current.capture.id}/next-round`, "POST", {}));

$("commit-enrollment").onclick = e => cameraAction(e.target, async () => {
  const result = await api(`/enrollments/${current.capture.id}/commit`, "POST", {
    mode: "DISARMED",
    expected_revision: current.mode_revision
  });
  show(result.active ? "Đã lưu. Chủ động bật giám sát sau khi kiểm tra." : "Đã lưu mẫu draft. Cần hiệu chỉnh trước khi kích hoạt.");
});

$("person-form").onsubmit = e => {
  e.preventDefault();
  action(e.submitter, async () => {
    await api("/people", "POST", {
      display_name: $("display-name").value,
      consent_recorded: $("consent").checked
    });
    e.target.reset();
  });
};

$("severity-filter").onchange = renderEvents;

function statusRow(root, key, value) {
  root.append(node("dt", key), node("dd", String(value)));
}

async function refresh() {
  if (!csrf || busy) return;
  busy = true;
  const epoch = viewEpoch;
  const t0 = performance.now();
  try {
    const status = await api("/status");
    if (epoch !== viewEpoch) return;
    lastRtt = Math.round(performance.now() - t0);
    current = status;
    cameraStatusValid = true;
    syncCamera();

    const latEl = $("metric-latency");
    if (latEl) {
      latEl.textContent = `Độ trễ API: ${lastRtt} ms`;
      latEl.className = "metric-pill " + (lastRtt < 120 ? "good" : lastRtt < 300 ? "warn" : "alert");
    }

    $("test").textContent = `Test còi ${current.ui_limits.test_cap_ms / 1000} giây`;
    $("history-retention").textContent = `100 sự kiện gần nhất. Ảnh bằng chứng: tối đa ${current.ui_limits.evidence_days} ngày, có thể dọn sớm theo quota.`;
    $("app").hidden = false;
    $("login-panel").hidden = true;
    $("logout").hidden = false;
    $("connection").textContent = current.device_online ? "Thiết bị online" : "Thiết bị offline";
    $("profile-banner").textContent = current.profile === "simulation" ? "ĐANG GIẢ LẬP — quan sát và còi không phải kết quả phần cứng hoặc độ chính xác AI." : "CAMERA THẬT — " + (current.calibration_error ? setupMessage(current.calibration_error) : current.active_people === 0 ? setupMessage("EMPTY_GALLERY") : "Hồ sơ hiệu chỉnh: " + current.calibration_id);
    $("mode-info").textContent = labels[current.mode] + (current.worker_error ? " · " + current.worker_error : "");
    const health = $("health");
    health.replaceChildren();
    statusRow(health, "AI", current.vision_error || current.worker_error || (current.vision_ready ? "Sẵn sàng" : "Chưa sẵn sàng"));
    statusRow(health, "Còi báo từ thiết bị", current.buzzer_reported);
    if (current.environment_quality) statusRow(health, "Chất lượng môi trường", current.environment_quality.usable
      ? `Đủ sáng theo ngưỡng ban đầu (luma ${current.environment_quality.median_luma})`
      : `Ảnh suy giảm: ${current.environment_quality.reason} — KHÔNG kết luận danh tính`);
    if (current.notification_error) statusRow(health, "Thông báo", setupMessage(current.notification_error));
    const notifications = current.notifications;
    if (notifications) {
      $("telegram-state").textContent = notifications.ready
        ? `Đã cấu hình Telegram · ${notifications.photos_enabled ? "có ảnh" : "chỉ nội dung"} · người quen: ${notifications.known_visits_enabled ? "bật" : "tắt"} · lượt chưa xác định: ${notifications.unresolved_visits_enabled ? "bật" : "tắt"}`
        : setupMessage(notifications.reason);
      if (notifications.photo_consent_required) $("telegram-state").textContent += " · cần xác nhận quyền gửi ảnh trên server";
      $("telegram-test").disabled = !notifications.ready;
      $("telegram-test-photo").disabled = !notifications.photos_enabled;
      if (!notifications.photos_enabled) $("telegram-test-photo").checked = false;
      const latest = notifications.latest;
      $("telegram-delivery").textContent = latest
        ? `Lần gần nhất: ${labels[latest.state] || latest.state}${latest.reason ? " · " + setupMessage(latest.reason) : ""}${latest.duplicate_possible ? " · có khả năng trùng do lần gửi trước chưa rõ" : ""}`
        : "Chưa có lượt gửi. Bấm Gửi thử Telegram khi sẵn sàng.";
    }
    statusRow(health, "Lệnh đang chờ", current.pending_command?.type || "Không có");
    statusRow(health, "Người quen active", current.active_people);
    statusRow(health, "Ảnh bỏ do thay mới", current.frames_replaced);

    statusRow(health, "FPS nhận từ camera", current.video.received_fps.toFixed(1));
    statusRow(health, "Nhịp AI", current.mode === "DISARMED" ? "Đã dừng" : `${current.analysis.interval_ms} ms · thực tế ${current.analysis.effective_fps.toFixed(1)} FPS`);
    statusRow(health, "Thời gian AI p95", current.analysis.duration_p95_ms === null ? "Chưa đo" : `${current.analysis.duration_p95_ms} ms${current.analysis.over_budget ? " · vượt ngân sách xử lý" : ""}`);
    const completed = current.recent_visits || [];
    const latestCompleted = completed.length ? completed[completed.length - 1].visit_id : null;
    if (latestCompleted !== lastCompletedVisit) {
      lastCompletedVisit = latestCompleted;
      nextListsRefresh = 0;
    }
    if (performance.now() >= nextListsRefresh) {
      const [people, receivedEvents] = await Promise.all([api("/people"), api("/events?limit=100")]);
      if (epoch !== viewEpoch) return;
      cachedPeople = people;
      events = receivedEvents;
      peopleById = new Map(people.map(p => [p.id, p.display_name]));
      nextListsRefresh = performance.now() + 3000;
      renderEvents();
    }
    renderPeople(cachedPeople);
    const tracks = $("tracks");
    tracks.replaceChildren();
    if (!current.tracks.length) tracks.append(node("p", "Chưa có đối tượng trong lượt quan sát."));
    for (const track of current.tracks) {
      const row = node("div", undefined, "track");
      row.append(node("strong", track.person_id ? `${labels[track.identity]}: ${peopleById.get(track.person_id) || "Hồ sơ"}` : labels[track.identity]), node("p", `${labels[track.direction]} · vùng ${track.zone}`));
      appendRecognitionDetails(row, track);
      if (track.visit_id) {
        const b = node("button", "Tắt còi lượt này");
        b.onclick = () => action(b, () => api("/system/silence", "POST", {
          visit_id: track.visit_id,
          idempotency_key: actionKey()
        }));
        row.append(b);
      }
      tracks.append(row);
    }
    for (const visit of completed.slice(-3).reverse()) {
      const row = node("div", undefined, "track");
      row.append(node("strong", `Lượt đã kết thúc: ${labels[visit.identity] || labels.UNCERTAIN}${visit.person_id ? ` · ${peopleById.get(visit.person_id) || "Hồ sơ"}` : ""}`));
      appendRecognitionDetails(row, visit);
      tracks.append(row);
    }
    renderEnrollment();
    if (cameraTarget === "camera-image") drawMonitorOverlay();
  } catch (e) {
    if (epoch !== viewEpoch) return;
    if (!current) { cameraStatusValid = false; stopCamera(); }
    show(e.message);
    $("connection").textContent = "Không cập nhật được";
  } finally {
    busy = false;
  }
}

function renderPeople(people) {
  $("people-count").textContent = `${people.length}/${current.enrollment_requirements.max_people} hồ sơ`;
  const root = $("people-list");
  root.replaceChildren();
  if (!people.length) root.append(node("p", "Chưa có hồ sơ. Tạo hồ sơ bên trên, sau đó bấm Đăng ký để mở camera và thu mẫu."));
  for (const person of people) {
    const row = node("article", undefined, "card person-row"),
      info = node("div"),
      buttons = node("div", undefined, "actions");
    info.append(node("strong", person.display_name), node("p", person.active ? "Đang dùng để nhận diện" : "Draft / đã vô hiệu"), node("div", person.id, "identifier"));
    for (const [label, path] of [
        ["Đăng ký", `/people/${person.id}/enrollment`],
        [person.active ? "Vô hiệu" : "Kích hoạt", `/people/${person.id}/${person.active?"disable":"activate"}`]
      ]) {
      const b = node("button", label);
      b.onclick = () => cameraAction(b, () => api(path, "POST", {}));
      buttons.append(b);
    }
    const del = node("button", "Xóa hoàn toàn");
    del.onclick = () => {
      if (confirm("Xóa mẫu khuôn mặt và ảnh bằng chứng trên server. Bản đã gửi Telegram và các backup cũ cần xóa riêng. Tiếp tục?")) cameraAction(del, () => api(`/people/${person.id}`, "DELETE"));
    };
    buttons.append(del);
    row.append(info, buttons);
    root.append(row);
  }
}

function renderEvents() {
  const root = $("event-list");
  root.replaceChildren();
  for (const event of events.filter(e => $("severity-filter").value === "all" || e.severity === $("severity-filter").value)) {
    const row = node("article", undefined, "card event " + event.severity);
    row.append(node("h3", labels[event.reason] || event.reason), node("p", new Date(event.created_at).toLocaleString("vi-VN") + " · " + event.source));
    row.append(node("p", `Còi: ${event.details.audible?(labels[event.command?.state]||"Chờ thiết bị"):(event.details.suppression||"Không phát lệnh")}. Thông báo: ${labels[event.notification?.state]||"Trong dashboard"}${event.notification?.reason ? " · " + setupMessage(event.notification.reason) : ""}.`));
    if (event.details.person_id) row.append(node("p", `Người: ${peopleById.get(event.details.person_id) || "Hồ sơ không còn active"}`));
    if (event.reason === "visit_completed") {
      row.append(node("p", `Kết quả: ${labels[event.details.identity] || labels.UNCERTAIN}`));
      appendRecognitionDetails(row, event.details);
    }
    const actions = node("div", undefined, "actions");
    const ack = node("button", event.acknowledged_at ? "Đã xem" : "Đánh dấu đã xem");
    ack.disabled = !!event.acknowledged_at;
    ack.onclick = () => action(ack, () => api(`/events/${event.id}/acknowledge`, "POST", {}));
    actions.append(ack);
    if (event.media_id) {
      const mediaId = event.media_id;
      const metaText = `${new Date(event.created_at).toLocaleString("vi-VN")} · ${labels[event.reason] || event.reason}`;
      const view = node("button", "Phóng to ảnh bằng chứng");
      view.onclick = () => openLightbox(mediaId, labels[event.reason] || event.reason, metaText);
      actions.append(view);

      const thumb = node("img", undefined, "event-thumb");
      thumb.alt = "Ảnh bằng chứng sự kiện (bấm để xem to)";
      thumb.src = `/api/v2/media/${mediaId}`;
      thumb.title = "Bấm để xem ảnh phóng to";
      thumb.onclick = () => openLightbox(mediaId, labels[event.reason] || event.reason, metaText);
      row.append(thumb);
    }
    row.append(actions);
    root.append(row);
  }
  if (!root.children.length) root.append(node("p", "Chưa có sự kiện phù hợp."));
}

const frameRenderer = new LatestFrameRenderer(frame => new Promise((resolve, reject) => {
  const url = URL.createObjectURL(new Blob([frame.jpeg], {type: "image/jpeg"}));
  const probe = new Image();
  const timeout = setTimeout(() => { probe.src = ""; URL.revokeObjectURL(url); reject(new Error("DECODE_TIMEOUT")); }, 2500);
  probe.onload = () => {
    clearTimeout(timeout);
    resolve({url, dispose: () => { if (imageUrl !== url) URL.revokeObjectURL(url); }});
  };
  probe.onerror = () => { clearTimeout(timeout); URL.revokeObjectURL(url); reject(new Error("DECODE_FAILED")); };
  probe.src = url;
}), (frame, decoded) => {
  if (!cameraTarget || frame.ageMs + performance.now() - frame.receivedAt > (current?.video?.stale_ms || 3000)) return;
  const target = cameraTarget, img = $(target);
  const previousUrl = imageUrl;
  imageUrl = decoded.url;
  img.onload = () => {
    if (cameraTarget !== target || imageUrl !== decoded.url) return;
    displayedFrame = frame.id;
    displayedMeta = frame;
    lastFrameId = frame.id;
    img.hidden = false;
    $(target === "camera-image" ? "image-empty" : "enrollment-image-empty").hidden = true;
    if (target === "camera-image") drawMonitorOverlay();
    else drawEnrollmentGuide();
    frameCount++;
    const now = performance.now(), elapsed = now - fpsStartTime;
    if (elapsed >= 1500) {
      currentFps = frameCount * 1000 / elapsed;
      frameCount = 0;
      fpsStartTime = now;
      $("metric-fps").textContent = `FPS hiển thị: ${currentFps.toFixed(1)}`;
    }
  };
  img.src = decoded.url;
  if (previousUrl) URL.revokeObjectURL(previousUrl);
});

function displayFrame(frame) {
  if (cameraTarget && frame.id !== lastFrameId) frameRenderer.submit(frame);
}

function drawMonitorOverlay() {
  const img = $("camera-image"), canvas = $("overlay");
  if (img.hidden || !img.naturalWidth || !img.complete) return;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d"), zones = current?.zones;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const [key, label] of [["approach", "A"], ["doorstep", "B"]]) {
    if (!zones) break;
    const [x,y,w,h] = zones[key];
    ctx.strokeStyle = key === "approach" ? "#ffca78" : "#72dec3";
    ctx.lineWidth = 2;
    ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "20px sans-serif";
    ctx.fillText(label,x*canvas.width+5,y*canvas.height+23);
  }
  const analysis = current?.latest_analysis;
  if (analysis?.frame_id !== displayedFrame) return;
  for (const o of analysis.observations) {
    ctx.strokeStyle = o.decision === "UNKNOWN" ? "#ff8d92" : "#72dec3";
    ctx.lineWidth = 3;
    ctx.strokeRect(o.bbox[0] * canvas.width, o.bbox[1] * canvas.height, o.bbox[2] * canvas.width, o.bbox[3] * canvas.height);
  }
}

setInterval(() => {
  const now = performance.now();
  const interval = current?.capture.active ? 500 : 1000;
  if (!document.hidden && now - lastStatusRefresh >= interval) {
    lastStatusRefresh = now;
    refresh();
  }
  const age = displayedMeta ? displayedMeta.ageMs + now - displayedMeta.receivedAt : null;
  $("image-age").textContent = age === null ? "Chưa có ảnh camera" : `Tuổi video: ${(age / 1000).toFixed(1)}s`;
  $("image-age").className = "metric-pill " + (age === null || age > 3000 ? "alert" : age > 1500 ? "warn" : "good");
}, 250);
refresh();
