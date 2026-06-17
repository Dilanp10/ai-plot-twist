# Static assets

Binary placeholders uploaded to R2 by
`apps/api/app/scripts/upload_static_assets.py`. The Cloudflare R2 public URL
of each file is wired into the runtime via `Settings`
(`generation_placeholder_url`, `generation_placeholder_video_url`).

## `placeholder.mp4`

Used by the T2V pipeline when one or more clips fall back from
`VideoProviderRouter` (`AllClipsFailedError` triggers the T2I fallback at
the coordinator level — the per-clip placeholder is only ever stitched
into the final mp4 when SOME clips succeed and the rest are individual
failures).

The committed binary is the minimal-valid 136-byte mp4 sentinel from
`app.providers.video.fake.MINIMAL_MP4`. It plays back as a 5-second
zero-frame moov atom — technically valid, visually empty.

### Regenerate with ffmpeg (recommended for production)

To replace the empty sentinel with a visible "..." card, run **once
locally** and commit the new binary:

```sh
ffmpeg -f lavfi -i color=c=black:s=512x512:d=5 \
       -vf "drawtext=text='...':fontcolor=white:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2" \
       -c:v libx264 -pix_fmt yuv420p \
       assets/placeholder.mp4
```

After regenerating, re-run `upload_static_assets.py` to publish the new
binary at the URL configured in `R2_PUBLIC_BASE_URL`.

## `placeholder.webp`

T2I fallback placeholder. NOT committed yet — wire the path here once
the image renders ship.
