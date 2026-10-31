"use strict";

// One bounded multipart reader per page. Frame metadata preserves overlay/freshness guards.
class CameraMultipartReader {
  constructor(onFrame) {
    this.onFrame = onFrame;
    this.buffer = new Uint8Array(0);
    this.part = null;
  }

  push(chunk) {
    // Drain large network reads incrementally; never retain an unbounded video queue.
    for (let offset = 0; offset < chunk.length; offset += 32768) {
      const piece = chunk.subarray(offset, offset + 32768);
      const joined = new Uint8Array(this.buffer.length + piece.length);
      joined.set(this.buffer);
      joined.set(piece, this.buffer.length);
      this.buffer = joined;
      this.drain();
    }
  }

  drain() {
    for (;;) {
      if (!this.part) {
        let end = -1;
        for (let i = 0; i + 3 < this.buffer.length; i++) {
          if (this.buffer[i] === 13 && this.buffer[i + 1] === 10 &&
              this.buffer[i + 2] === 13 && this.buffer[i + 3] === 10) {
            end = i;
            break;
          }
        }
        if (end < 0) {
          if (this.buffer.length > 4096) throw new Error("STREAM_HEADER_TOO_LARGE");
          return;
        }
        if (end > 4096) throw new Error("STREAM_HEADER_TOO_LARGE");
        const header = new TextDecoder().decode(this.buffer.subarray(0, end));
        if (!/^(?:\r\n)?--frame\r\n/i.test(header)) throw new Error("STREAM_BOUNDARY_INVALID");
        const length = /^Content-Length:\s*(\d+)\s*$/im.exec(header);
        const id = /^X-Frame-Id:\s*([a-z0-9-]{1,64})\s*$/im.exec(header);
        const age = /^X-Frame-Age-Ms:\s*(\d+)\s*$/im.exec(header);
        const revision = /^X-Video-Revision:\s*(\d+)\s*$/im.exec(header);
        if (!/^Content-Type:\s*image\/jpeg\s*$/im.test(header) || !length || !id || !age || !revision) {
          throw new Error("STREAM_METADATA_INVALID");
        }
        this.part = {length: Number(length[1]), id: id[1], ageMs: Number(age[1]), revision: Number(revision[1]), startedAt: performance.now()};
        if (this.part.length < 1 || this.part.length > 524288 || this.part.ageMs > 5000 ||
            !Number.isSafeInteger(this.part.revision)) {
          throw new Error("STREAM_FRAME_INVALID");
        }
        this.buffer = this.buffer.slice(end + 4);
      }
      if (this.buffer.length < this.part.length) return;
      const jpeg = this.buffer.slice(0, this.part.length);
      this.buffer = this.buffer.slice(this.part.length);
      const part = this.part;
      this.part = null;
      this.onFrame({id: part.id, ageMs: part.ageMs + performance.now() - part.startedAt, revision: part.revision, jpeg});
    }
  }
}

class CameraStream {
  constructor({onFrame, onClear, onState, onUnauthorized, getCsrf = () => "", getSettings = () => ({})}) {
    Object.assign(this, {onFrame, onClear, onState, onUnauthorized, getCsrf, getSettings});
    this.generation = 0;
    this.key = null;
    this.leaseId = null;
    this.controller = this.leaseController = null;
    this.retry = this.watchdog = this.renewTimer = null;
    this.renewing = false;
  }

  release(id, csrf) {
    if (!id) return;
    fetch(`/api/v2/video/viewers/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: {"X-CSRF-Token": csrf},
      credentials: "same-origin", keepalive: true
    }).catch(() => {});
  }

  stop() {
    this.generation++;
    this.key = null;
    clearTimeout(this.retry);
    clearTimeout(this.watchdog);
    clearTimeout(this.renewTimer);
    this.retry = this.watchdog = this.renewTimer = null;
    this.controller?.abort();
    this.leaseController?.abort();
    this.controller = this.leaseController = null;
    this.renewing = false;
    this.release(this.leaseId, this.getCsrf());
    this.leaseId = null;
    this.videoRevision = null;
    this.onClear();
  }

  start(key, enrollmentId = null) {
    if (this.key === key) {
      if (this.enrollmentId !== enrollmentId) {
        this.enrollmentId = enrollmentId;
        clearTimeout(this.renewTimer);
        this.renew(this.generation);
      }
      return;
    }
    this.stop();
    this.key = key;
    this.enrollmentId = enrollmentId;
    this.renew(this.generation);
  }

  async renew(generation) {
    if (generation !== this.generation || !this.key || this.renewing) return;
    this.renewing = true;
    const enrollmentId = this.enrollmentId;
    const controller = new AbortController();
    this.leaseController = controller;
    const timeout = setTimeout(() => controller.abort(), 3000);
    let delay = this.getSettings().viewer_renew_ms || 5000;
    try {
      const response = await fetch("/api/v2/video/viewers", {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-CSRF-Token": this.getCsrf()},
        body: JSON.stringify({lease_id: this.leaseId, enrollment_session_id: enrollmentId})
      });
      if (generation !== this.generation) return;
      if (response.status === 401) { this.stop(); this.onUnauthorized(); return; }
      if (response.status === 404) {
        this.leaseId = null;
        this.controller?.abort();
        throw new Error("VIEW_EXPIRED");
      }
      if (!response.ok) throw new Error("VIEW_UNAVAILABLE");
      const view = await response.json();
      if (generation !== this.generation) { this.release(view.lease_id, this.getCsrf()); return; }
      if (typeof view.lease_id !== "string" || !Number.isFinite(view.remaining_ms)) throw new Error("VIEW_INVALID");
      this.leaseId = view.lease_id;
      this.leaseExpires = performance.now() + Math.max(0, view.remaining_ms - 3000);
      if (!this.controller) this.connect(generation);
      if (enrollmentId !== this.enrollmentId) delay = 0;
    } catch (_) {
      if (generation !== this.generation) return;
      delay = 1000;
      if (!this.leaseId || performance.now() >= this.leaseExpires) {
        this.controller?.abort();
        this.onClear();
      }
      this.onState("Chưa gia hạn được kết nối camera; đang thử lại…");
    } finally {
      clearTimeout(timeout);
      if (generation === this.generation && this.key) {
        this.renewing = false;
        this.renewTimer = setTimeout(() => this.renew(generation), delay);
      }
    }
  }

  watch(generation, remainingMs) {
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      if (generation !== this.generation) return;
      this.watchdog = null;
      this.onClear();
      this.onState("Chưa có ảnh mới; đang kết nối lại…");
      this.controller?.abort();
    }, Math.max(0, remainingMs));
  }

  async connect(generation) {
    if (generation !== this.generation || !this.key || !this.leaseId || this.controller) return;
    const controller = new AbortController();
    this.controller = controller;
    const staleMs = this.getSettings().stale_ms || 3000;
    if (this.watchdog === null) this.watch(generation, staleMs);
    this.onState("Đang kết nối camera…");
    let reader = null, response = null, retryMs = 250;
    try {
      response = await fetch(`/api/v2/live.mjpg?lease_id=${encodeURIComponent(this.leaseId)}`, {
        signal: controller.signal, credentials: "same-origin", cache: "no-store"
      });
      if (generation !== this.generation) return;
      if (response.status === 401) { this.stop(); this.onUnauthorized(); return; }
      if (!response.ok || !response.body ||
          !/^multipart\/x-mixed-replace\s*;\s*boundary="?frame"?\s*$/i.test(response.headers.get("Content-Type") || "")) {
        retryMs = response.status === 429 ? 2000 : 1000;
        throw new Error("STREAM_UNAVAILABLE");
      }
      reader = response.body.getReader();
      const parser = new CameraMultipartReader(frame => {
        if (generation !== this.generation || controller.signal.aborted) return;
        if (this.videoRevision !== null && frame.revision !== this.videoRevision) this.onClear();
        this.videoRevision = frame.revision;
        if (frame.ageMs >= staleMs) throw new Error("STREAM_FRAME_STALE");
        this.watch(generation, staleMs - frame.ageMs);
        this.onState("Đang nhận video trực tiếp");
        this.onFrame({...frame, receivedAt: performance.now()});
      });
      for (;;) {
        const {value, done} = await reader.read();
        if (generation !== this.generation || controller.signal.aborted) return;
        if (done) break;
        parser.push(value);
      }
    } catch (_) {
      if (generation !== this.generation) return;
      this.onClear();
      this.onState("Mất luồng ảnh; đang thử lại…");
      retryMs = Math.max(retryMs, 1000);
    } finally {
      if (reader) {
        try { await reader.cancel(); } catch (_) { /* aborted reader */ }
        reader.releaseLock();
      } else {
        try { await response?.body?.cancel(); } catch (_) { /* no reader */ }
      }
      if (generation === this.generation && this.key) {
        if (this.controller === controller) this.controller = null;
        clearTimeout(this.retry);
        this.retry = setTimeout(() => this.connect(generation), retryMs);
      }
    }
  }
}

// Exactly one decode plus one newest pending image. No scheduled-frame FIFO.
class LatestFrameRenderer {
  constructor(decode, paint) {
    Object.assign(this, {decode, paint});
    this.generation = 0;
    this.pending = null;
    this.running = false;
  }
  clear() { this.generation++; this.pending = null; }
  submit(frame) {
    this.pending = frame;
    if (!this.running) this.drain();
  }
  async drain() {
    this.running = true;
    try {
      while (this.pending) {
        const frame = this.pending, generation = this.generation;
        this.pending = null;
        let decoded;
        try {
          decoded = await this.decode(frame);
          if (generation === this.generation) this.paint(frame, decoded);
        } catch (_) { /* discard invalid JPEG; the stream watchdog handles source loss */ }
        finally { decoded?.dispose(); }
      }
    } finally { this.running = false; }
  }
}
