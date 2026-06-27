/**
 * Admin API client — typed wrappers around /api/v1/admin/* endpoints.
 *
 * Module 014 / Task T-006.
 *
 * Uses a separate token (admin JWT) stored in sessionStorage so it never
 * mixes with the user JWT in authStore. Token expires with the browser tab.
 */

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'aipt_admin_token';

export function getAdminToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setAdminToken(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // sessionStorage unavailable (private mode)
  }
}

export function clearAdminToken(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AdminCycleResponse {
  cycle_state: string;
  cycle_date: string;
  state_entered_at: string;
  chapter_id: number;
  next_chapter_id: number | null;
  winner: {
    twist_text: string;
    vote_count: number;
    author_display_name: string;
    character_slug: string;
    character_name: string;
    character_photo_url: string;
  } | null;
}

export interface VideoUploadUrlResponse {
  upload_url: string;
  public_url: string;
  key: string;
}

// ---------------------------------------------------------------------------
// Internal fetch helper
// ---------------------------------------------------------------------------

type AdminResult<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

async function adminFetch<T>(
  url: string,
  init: RequestInit = {},
  token?: string | null,
): Promise<AdminResult<T>> {
  const headers = new Headers(init.headers as HeadersInit | undefined);
  headers.set('Content-Type', 'application/json');
  const t = token ?? getAdminToken();
  if (t) headers.set('Authorization', `Bearer ${t}`);

  try {
    const resp = await fetch(url, { ...init, headers });
    if (resp.ok) {
      const data = (await resp.json()) as T;
      return { ok: true, data };
    }
    let message = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore parse failure
    }
    return { ok: false, status: resp.status, message };
  } catch (err) {
    return { ok: false, status: 0, message: err instanceof Error ? err.message : 'Error de red' };
  }
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function adminLogin(password: string): Promise<AdminResult<{ token: string }>> {
  return adminFetch<{ token: string }>(
    '/api/v1/admin/auth',
    { method: 'POST', body: JSON.stringify({ password }) },
    null,
  );
}

export async function adminGetCycle(): Promise<AdminResult<AdminCycleResponse>> {
  return adminFetch<AdminCycleResponse>('/api/v1/admin/cycle', { method: 'GET' });
}

export async function adminGetUploadUrl(
  chapterId: number,
): Promise<AdminResult<VideoUploadUrlResponse>> {
  return adminFetch<VideoUploadUrlResponse>(
    `/api/v1/admin/chapters/${chapterId}/video-upload-url`,
    { method: 'POST' },
  );
}

export async function adminConfirmVideo(
  chapterId: number,
  videoUrl: string,
): Promise<AdminResult<{ chapter_id: number; video_url: string }>> {
  return adminFetch<{ chapter_id: number; video_url: string }>(
    `/api/v1/admin/chapters/${chapterId}/video`,
    { method: 'PUT', body: JSON.stringify({ video_url: videoUrl }) },
  );
}

// ---------------------------------------------------------------------------
// Direct R2 upload with progress
// ---------------------------------------------------------------------------

export function uploadToR2(
  uploadUrl: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', uploadUrl);
    xhr.setRequestHeader('Content-Type', 'video/mp4');

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        onProgress(Math.round((evt.loaded / evt.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`R2 upload failed: ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('Error de red al subir a R2'));
    xhr.send(file);
  });
}
