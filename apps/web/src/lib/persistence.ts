/**
 * Thin IndexedDB wrapper for auth credentials.
 *
 * Stores two keys (`jwt` and `deviceSecret`) in a single object-store
 * (`auth`) inside the `aiplottwist` database. The connection is cached
 * for the lifetime of the page.
 *
 * Public API:
 *   getAuth()            → { jwt?, deviceSecret? }
 *   setAuth({ jwt, deviceSecret })
 *   clearAuth()
 *
 * Testing helpers (don't use in production code):
 *   _resetDB()  — clears the cached connection so tests can inject a
 *                 fresh fake-indexeddb instance.
 */

const DB_NAME = 'aiplottwist';
const DB_VERSION = 1;
const STORE_AUTH = 'auth';

// Module-level connection cache (one per page load).
let _dbPromise: Promise<IDBDatabase> | null = null;

function _openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_AUTH)) {
        db.createObjectStore(STORE_AUTH);
      }
    };

    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function getDB(): Promise<IDBDatabase> {
  if (!_dbPromise) _dbPromise = _openDB();
  return _dbPromise;
}

/** Reset the cached DB connection.  Only for tests — call before each test
 *  that needs a clean IndexedDB state. */
export function _resetDB(): void {
  _dbPromise = null;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface AuthData {
  jwt?: string;
  deviceSecret?: string;
}

/** Read both credentials from IndexedDB in a single transaction. */
export async function getAuth(): Promise<AuthData> {
  const db = await getDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_AUTH, 'readonly');
    const store = tx.objectStore(STORE_AUTH);
    const jwtReq = store.get('jwt');
    const secretReq = store.get('deviceSecret');

    tx.oncomplete = () =>
      resolve({ jwt: jwtReq.result as string | undefined,
                deviceSecret: secretReq.result as string | undefined });
    tx.onerror = () => reject(tx.error);
  });
}

/** Persist both credentials atomically. */
export async function setAuth(v: { jwt: string; deviceSecret: string }): Promise<void> {
  const db = await getDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_AUTH, 'readwrite');
    const store = tx.objectStore(STORE_AUTH);
    store.put(v.jwt, 'jwt');
    store.put(v.deviceSecret, 'deviceSecret');
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/** Delete both credentials atomically. */
export async function clearAuth(): Promise<void> {
  const db = await getDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_AUTH, 'readwrite');
    const store = tx.objectStore(STORE_AUTH);
    store.delete('jwt');
    store.delete('deviceSecret');
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
