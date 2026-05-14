/**
 * AudioManager – Global singleton that enforces exclusive audio playback.
 *
 * DESIGN:
 * - Only ONE audio owner (component) can play at a time.
 * - When a new owner acquires the lock, the previous owner is forcibly paused.
 * - DockPlayer registers its <video> element; ClipEditorV2 acquires the lock on open.
 * - Even if React re-renders or closures are stale, the physical video element
 *   is monitored via a "playing" event listener that auto-pauses when not the owner.
 *
 * USAGE:
 *   import audioManager from '@/lib/audioManager';
 *
 *   // DockPlayer: register video element
 *   audioManager.registerPlayer('dock', videoElement);
 *   audioManager.acquire('dock');  // DockPlayer wants to play
 *
 *   // ClipEditorV2: acquire lock on open
 *   audioManager.acquire('clipEditor');
 *
 *   // ClipEditorV2: release lock on close
 *   audioManager.release('clipEditor');
 */

class AudioManager {
  constructor() {
    /** @type {string|null} Current owner ID that has exclusive audio rights */
    this.currentOwner = null;

    /** @type {Map<string, HTMLVideoElement|HTMLAudioElement>} Registered media elements */
    this.players = new Map();

    /** @type {Map<string, Function>} Event listeners for cleanup */
    this._listeners = new Map();

    /** @type {Set<string>} Owners that are currently locked out (cannot play) */
    this._lockedOut = new Set();
  }

  /**
   * Register a media element (video/audio) with a unique owner ID.
   * Attaches a "playing" event guard that auto-pauses if not the current owner.
   */
  registerPlayer(ownerId, element) {
    if (!element) return;

    // Clean up previous registration for this owner
    this.unregisterPlayer(ownerId);

    this.players.set(ownerId, element);

    // Physical guard: if video starts playing but this owner doesn't have the lock, pause it
    const guard = () => {
      if (this._lockedOut.has(ownerId)) {
        element.pause();
        element.muted = true;
        console.warn(`[AudioManager] Blocked unauthorized play from "${ownerId}" (locked out by "${this.currentOwner}")`);
      }
    };

    element.addEventListener('playing', guard);
    this._listeners.set(ownerId, guard);
  }

  /**
   * Unregister a media element and remove its guard listener.
   */
  unregisterPlayer(ownerId) {
    const element = this.players.get(ownerId);
    const listener = this._listeners.get(ownerId);
    if (element && listener) {
      element.removeEventListener('playing', listener);
    }
    this.players.delete(ownerId);
    this._listeners.delete(ownerId);
    this._lockedOut.delete(ownerId);
  }

  /**
   * Acquire exclusive audio rights for an owner.
   * All other registered players are paused and locked out.
   */
  acquire(ownerId) {
    this.currentOwner = ownerId;

    // Lock out and pause all OTHER players
    for (const [id, element] of this.players.entries()) {
      if (id !== ownerId) {
        this._lockedOut.add(id);
        if (element && !element.paused) {
          element.pause();
        }
        if (element) {
          element.muted = true;
        }
      }
    }

    // Ensure the acquiring owner is NOT locked out
    this._lockedOut.delete(ownerId);
  }

  /**
   * Release audio rights for an owner.
   * If this owner was the current owner, unlock all other players.
   */
  release(ownerId) {
    if (this.currentOwner === ownerId) {
      this.currentOwner = null;
      // Unlock all players
      this._lockedOut.clear();

      // Unmute previously locked players
      for (const [id, element] of this.players.entries()) {
        if (id !== ownerId && element) {
          element.muted = false;
        }
      }
    }
  }

  /**
   * Check if an owner is currently allowed to play audio.
   */
  canPlay(ownerId) {
    return !this._lockedOut.has(ownerId);
  }

  /**
   * Check if a specific owner currently holds the audio lock.
   */
  isOwner(ownerId) {
    return this.currentOwner === ownerId;
  }
}

// Singleton instance
const audioManager = new AudioManager();

// Expose on window for debugging in dev tools
if (typeof window !== 'undefined') {
  window.__audioManager = audioManager;
}

export default audioManager;
