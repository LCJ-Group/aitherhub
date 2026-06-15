# Video Shaking Bug Analysis V2

## Video: pasted_file_BhGTWu_20260615-130950.mp4

This is a screen recording of a generated clip being played back. The clip is 1:30 long.
The recording shows the clip at 0:17-0:18 mark.

## Visual Issues Identified:
1. **Continuous rapid shaking/jittering** - the entire frame vibrates constantly
2. **Unnatural pulsing zoom** - rapid zoom in/out that never stops
3. **Brightness flashes/flickering** - subtle but noticeable fluctuations
4. The combination makes the video "unwatchable" (user's words: 不能看)

## Key Insight from User:
"我们视频的画面要协调能看，而不是为了剪辑的规则去剪辑" 
= "Our videos need to look natural and watchable, not edited just for the sake of editing rules"

## Root Cause Analysis:
The V2.34.3 fix only addressed the CUT BOUNDARY zoom bursts, but the video ALSO has:
1. **Audio peak zoom pulses** (from _generate_zoom_keyframes) - up to 8 zooms at 1.08x, 0.4s each
2. **deflicker filter** - `deflicker=size=5:mode=am` which can cause frame-to-frame brightness jitter
3. **Speed factor** (1.05x) - setpts causing slight frame timing issues

The REAL fix needs to be more aggressive:
- The deflicker filter should be REMOVED (it was meant to fix lighting flicker but is causing visual instability)
- Audio peak zooms should be much gentler (1.03-1.05x max) or disabled entirely
- The overall philosophy should be: STABILITY > EFFECTS

## This clip was generated BEFORE V2.34.3 deploy
The V2.34.3 fix hasn't been applied to this clip yet. But even with V2.34.3, the audio peak
zooms (up to 8 at 1.08x) would still cause visible shaking.

## Required Fix:
1. Remove deflicker filter entirely
2. Reduce audio peak zoom from 1.08x to 1.03x maximum
3. Reduce zoom pulse count from max 8 to max 4
4. Consider disabling zoom pulse entirely for clips with many cuts
