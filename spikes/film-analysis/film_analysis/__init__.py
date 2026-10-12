"""Film-analysis worker: fills the content model (panorama.content/0) for one video file.

Modules, one per field of the content model:
  frame_table      timeline rule, frame table in display order, frame-rate facts
  file_facts       streams, colour, HDR / Dolby Vision, chapters
  black_level, bars_crop, bars_text   black level, crop rectangles, text in the bars
  shots_transnet   shot boundaries (TransNetV2 on ONNX Runtime)
  slides_motion    camera slides, per-frame positions, held drawings, motion and brightness
  subjects_faces   faces / heads / people, keep-clear map
  subtitles_events subtitle events per track, class and rendered box
Plumbing: proc (processes, priority, job object), media_probe (ffprobe), decode_paths (hardware decode choice),
signals_store (raw per-frame columns), segments, segment_worker, gpu_inference, prescan, scan, manifest (index, stale tables),
derive_spine / derive_tables / derive_slides_subjects (the tables made from the signals in seconds), refresh (making stale
tables again), file_key (cache key), versions (analyzer versions), content_reader (reading the finished model).
"""

SCHEMA_ID = "panorama.content/0"
WORKER_VERSION = "0.1.0"
