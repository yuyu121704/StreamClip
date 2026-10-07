"""Offline regressions for the audited workflow. Run: python -B workflow_test.py."""
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import json
import tempfile
import io
import urllib.error

import app


def check_private_visual_review() -> None:
    with tempfile.TemporaryDirectory(prefix="private-review-check-") as folder:
        root = Path(folder)
        settings = app.Settings(base_dir=folder, auto_slice=True, publish_visibility="public", llm_model="test-vision", subtitle_burn_enabled=False, publish_interval_seconds=0)
        settings.ensure_dirs()
        db = app.Database(root / "app.db")
        service = app.RecorderService(settings, db, app.queue.Queue())
        source = root / "source.mp4"
        source.write_bytes(b"offline source")
        rid = db.create_recording("1", "review", "复核回归", str(source), app.now_text(), source_type="local", source_id="offline-source")
        db.finish_recording(rid, "complete", str(source), app.now_text(), 90)
        segments = [{"start": 0, "end": 30, "text": "一开始没想到能过关。"}, {"start": 30, "end": 60, "text": "结果挑战成功了。"}]
        app.write_json_atomic(source.with_suffix(".transcript.json"), {
            "segments": segments, "asr_metadata": {"audio_timeline_signature": app.asr_timeline_signature(source)},
        })
        candidate = {"source": "llm", "start": 0, "end": 60, "title": "挑战成功了", "cover_text": "挑战成功了", "reason": "一场挑战的结果", "review_flags": ["privacy"]}
        package_path = source.with_suffix(".publish.json")
        app.write_json_atomic(package_path, {"candidates": [{**candidate, "render_interval": {"start": 0, "end": 60}}]})
        scheduled = []

        def frame_command(args, _timeout):
            frame = app.Image.new("RGB", (640, 360), "#334455")
            app.ImageDraw.Draw(frame).rectangle((200, 50, 440, 330), fill="#DDCCAA")
            frame.save(args[-1])
            return 0, "", ""

        def render(_source, destination, *_args):
            destination.write_bytes(b"offline rendered clip")

        def thumbnail(_source, destination, *_args):
            app.Image.new("RGB", (1280, 720), "#445566").save(destination)

        try:
            with patch.object(service, "_schedule_task", side_effect=lambda task, work: scheduled.append((task, work))), patch.object(service.ffmpeg, "has_video", return_value=True), patch.object(service.ffmpeg, "_run", side_effect=frame_command), patch.object(service.ffmpeg, "clip", side_effect=render) as rendered, patch.object(service.ffmpeg, "thumbnail", side_effect=thumbnail), patch.object(app.LLMClient, "vision", return_value='{"approved":false,"frame_index":0,"reason":"标题超出逐字证据"}'):
                service._auto_slice(db.get_recording(rid), [candidate])
                rendered.assert_called_once()
            clip = db.list_clips()[0]
            cid = clip["id"]
            assert clip["status"] == "complete" and Path(clip["path"]).is_file()
            app.validate_cover(Path(clip["thumbnail_path"]))
            state, flags, metadata = db.clip_review(cid)
            assert state == "manual_review" and "privacy" in flags
            assert metadata["visual_review"]["approved"] is False and metadata["visual_review"]["selected_frame_index"] >= 1
            assert "cover_checked" not in metadata["state_history"]
            assert json.loads(package_path.read_text(encoding="utf-8"))["candidates"][0]["visual_review"]["approved"] is False
            clip_task = next(task for task in db.list_tasks() if task["kind"] == "clip")
            assert clip_task["status"] == "complete" and "仅限私密投稿" in clip_task["message"] and not clip_task["error"]
            upload = db.list_uploads()[0]
            uid, tid = upload["id"], upload["task_id"]
            queued = json.loads(upload["metadata_json"])
            assert queued["visibility"] == "self" and "标题超出逐字证据" in queued["review_warning"]
            assert len(scheduled) == 1 and scheduled[0][0] == tid

            # Cached media and editable packages cannot erase the rendered review.
            conflicting = {**candidate, "visual_review": {"approved": True, "reason": "旧结果"}}
            app.write_json_atomic(package_path, {"candidates": [{**conflicting, "render_interval": {"start": 0, "end": 60}, "review_status": "ready"}]})
            with patch.object(service.ffmpeg, "review_cover", side_effect=AssertionError("cached clip rendered again")):
                assert service._create_clip_sync(rid, 0, 60, candidate["title"], True, candidate=conflicting) == cid
            assert app.visual_review_warning(db.clip_review(cid)[2])
            assert service.enqueue_upload(cid, start=False, force=True) == uid
            assert len(db.list_uploads()) == 1 and settings.publish_visibility == "public"
            override_uid = service.enqueue_upload(cid, start=False, account_id=123, force=True)
            assert json.loads(db.get_upload(override_uid)["metadata_json"])["visibility"] == "self"

            # Retrying the previously failed automatic clip must also enqueue its upload.
            payload = {"recording_id": rid, "start": 0, "end": 60, "title": candidate["title"], "candidate": candidate, "auto": True}
            failed, _ = db.create_task("clip", "old-visual-rejection", payload)
            db.update_task(failed, status="error", error="AI 复核未通过")
            with patch.object(service, "_schedule_task", side_effect=lambda task, work: scheduled.append((task, work))):
                service.retry_task(failed)
                with patch.object(service, "enqueue_upload", wraps=service.enqueue_upload) as enqueue:
                    scheduled[-1][1]()
                    enqueue.assert_called_once_with(cid, start=True, auto=True)
            assert db.get_task(failed)["status"] == "complete" and "仅限私密投稿" in db.get_task(failed)["message"] and not db.get_task(failed)["error"]

            # Even an older public queue record is clamped again by the worker.
            db.set_upload_metadata(uid, {"visibility": "public"})
            with patch.object(service, "_cookie_for", side_effect=RuntimeError("account unavailable")):
                service._upload_worker(uid, tid)
            assert json.loads(db.get_upload(uid)["metadata_json"])["visibility"] == "self"
            db.update_task(tid, status="queued")
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "upload", side_effect=RuntimeError("offline upload failure")) as uploader:
                service._upload_worker(uid, tid)
            assert uploader.call_args.args[-2] == "self" and db.get_upload(uid)["status"] == "error"
            assert json.loads(db.get_upload(uid)["metadata_json"])["visibility"] == "self"

            # A queued reminder survives retry, later review edits and platform confirmation.
            db.set_clip_review(cid, "ready", [], {"visual_review": {"approved": True}, "review_override": True})
            def submit(*args):
                assert args[-2] == "self"
                args[-1]({"cid": 123, "visibility": "self"})
                return "BV-private-review"
            with patch.object(service, "_schedule_task", side_effect=lambda task, work: scheduled.append((task, work))):
                service.retry_upload(uid)
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "upload", side_effect=submit), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV-private-review", "state": -50, "is_only_self": 1}):
                scheduled[-1][1]()
            assert db.get_upload(uid)["status"] == "success"
            assert "标题超出逐字证据" in db.get_task(tid)["message"] and not db.get_task(tid)["error"]
            with patch.object(service, "_schedule_task", side_effect=lambda task, work: scheduled.append((task, work))):
                service.retry_upload(uid)
            with patch.object(app.BilibiliUploader, "upload", side_effect=AssertionError("successful post submitted again")):
                scheduled[-1][1]()
            assert "仅限私密投稿" in db.get_task(tid)["message"]

            normal = db.create_clip(rid, "挑战完整经过", 0, 60, str(source), metadata={"source": "llm", "visual_review": {"approved": True}}, review_status="ready")
            thumbnail(source, source.with_suffix(".jpg"))
            db.set_clip_status(normal, "complete")
            package_path.unlink()
            normal_uid = service.enqueue_upload(normal, start=False)
            normal_metadata = json.loads(db.get_upload(normal_uid)["metadata_json"])
            assert normal_metadata["visibility"] == "public" and not normal_metadata["review_warning"]
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "upload", return_value="BV-public") as uploader, patch.object(service, "_refresh_upload_receipt"):
                service._upload_worker(normal_uid, db.get_upload(normal_uid)["task_id"])
            assert uploader.call_args.args[-2] == "public"
        finally:
            service.executor.shutdown(wait=True)
    print("Private review passed: render, fallback frame, forced private queue/retry, durable warning, receipt and normal public posts")


def run() -> None:
    check_private_visual_review()
    with tempfile.TemporaryDirectory(prefix="workflow-check-") as folder:
        root = Path(folder)
        settings = app.Settings(base_dir=folder, auto_slice=False, llm_model="", dashscope_api_key="offline-test")
        settings.ensure_dirs()
        db = app.Database(root / "app.db")
        service = app.RecorderService(settings, db, app.queue.Queue())
        try:
            started = "2026-09-08T22:00:00+08:00"
            clock = datetime.fromisoformat(started).timestamp()
            collector = app.DanmakuCollector(None, "1", root / "chat.jsonl", 0, clock, 5, lambda _: None)
            assert collector._history_event({"timeline": "2026-09-08 22:00:10", "text": "chat"})["offset"] == 10
            assert collector._history_event({"timeline": "2026-09-08T14:00:10Z", "text": "chat"})["offset"] == 10
            assert app._wall_timestamp("2026-09-08 14:00:10") == datetime(2026, 9, 8, 14, 0, 10, tzinfo=timezone.utc).timestamp()
            rows = [{"offset": 28810, "timestamp": "2026-09-08T14:00:12Z", "text": "legacy"}, {"offset": 20, "timestamp": "2026-09-08T14:00:22Z", "text": "new", "clock_version": 2}]
            chat = root / "chat.jsonl"
            chat.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            before = chat.read_bytes()
            assert [row["offset"] for row in app.load_danmaku_sidecar(chat, 90, started)] == [10, 20]
            assert chat.read_bytes() == before
            source = root / "sample.mp4"
            source.write_bytes(b"isolated media placeholder")
            rid = db.create_recording("1", "check", "回归录播", str(source), started, source_type="local", source_id="check-source")
            db.finish_recording(rid, "complete", str(source), app.now_text(), 90)
            task_id, _ = db.create_task("analysis", "asr-failure", {"recording_id": rid})
            with patch.object(service.ffmpeg, "extract_audio"), patch.object(service.transcriber, "transcribe", side_effect=RuntimeError("test ASR failure")), patch.object(service, "_auto_slice") as slices:
                service._analyze_recording(rid, task_id)
                slices.assert_not_called()
            assert db.get_task(task_id)["status"] == "error"
            assert "test ASR failure" in db.get_recording(rid)["error"]
            assert not source.with_suffix(".transcript.json").exists()
            segments = [{"start": 0, "end": 30, "text": "她以为今天可以直接过关。"}, {"start": 30, "end": 60, "text": "最后终于通过挑战，大家都很开心。"}]
            transcript = source.with_suffix(".transcript.json")
            transcript.write_text(json.dumps({"segments": segments}), encoding="utf-8")
            db.update_recording_analysis(rid, str(transcript), "挑战回顾", [])
            flags = app.review_flags_for_candidate({"start": 0, "end": 60, "title": "讨论歌曲与翻唱"}, segments, [], db.get_recording(rid))
            assert "music_content" in flags and not set(flags) & app.HARD_REVIEW_FLAGS
            flags = app.review_flags_for_candidate({"start": 0, "end": 60, "title": "作者明确禁止转载"}, segments, [], db.get_recording(rid))
            assert "copyright_restricted" in flags
            pool = [{"start": index * 100, "end": index * 100 + 60, "title": f"候选{index}", "confidence": 0.9} for index in range(17)]
            pool_segments = [{**segment, "start": segment["start"] + index * 100, "end": segment["end"] + index * 100} for index in range(17) for segment in segments]
            decisions = {"selected": [{**pool[index - 1], "id": index, "title": "挑战一波三折，终于过关了", "title_candidates": ["挑战一波三折，终于过关了"], "cover_text": "以为能直接过\n过关可真不容易", "reason": "合成事件", "watch_reason": "合成挑战的结果", "title_evidence": pool_segments[(index - 1) * 2:index * 2]} for index in [1, 5, 8]], "rejected": [{"id": index, "reason": "重复短问答"} for index in range(1, 18) if index not in [1, 5, 8]], "reason": "保留三个独立事件，舍弃重复短问答"}
            with patch.object(app.LLMClient, "chat", return_value=json.dumps(decisions, ensure_ascii=False)):
                curated = app.curate_highlights(pool, 3134, settings, lambda _: None, pool_segments)
            assert [item["start"] for item in curated] == [0, 400, 700]
            assert app.normalize_highlights(curated, 3134)[0]["editorial_review"]["limit"] == settings.max_highlights
            with patch.object(app.LLMClient, "chat", return_value=json.dumps({"selected": [], "rejected": [{"id": index, "reason": "没有独立看点"} for index in range(1, 18)], "reason": "没有值得投稿的独立事件"})):
                assert app.curate_highlights(pool, 3134, settings, lambda _: None, pool_segments) == []
            with patch.object(app.LLMClient, "chat", return_value='{"selected":[1,1]}'):
                try:
                    app.curate_highlights(pool, 3134, settings, lambda _: None, pool_segments)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("duplicate editorial IDs accepted")
            cid = db.create_clip(rid, "挑战成功", 0, 60, str(source), metadata={"source": "llm", "title": "挑战成功", "reason": "终于过关", "cover_text": "挑战开始\n终于成功"}, review_status="rendered")
            db.set_clip_status(cid, "complete")
            try:
                service.enqueue_upload(cid, start=False)
            except RuntimeError as exc:
                assert "封面" in str(exc)
            else:
                raise AssertionError("missing cover was queued")
            cover = source.with_suffix(".jpg")
            app.Image.new("RGB", (1280, 720), "#445566").save(cover)
            with ThreadPoolExecutor(max_workers=6) as workers:
                ids = list(workers.map(lambda _: service.enqueue_upload(cid, start=False), range(12)))
            assert len(set(ids)) == 1 and len(db.list_uploads()) == 1
            upload_id = ids[0]
            task_id = db.get_upload(upload_id)["task_id"]
            calls = []

            def timed_out_upload(*args):
                calls.append("submit")
                args[-1]({"cid": 1234, "visibility": "self"})
                raise TimeoutError("response lost after POST")

            with patch.object(service, "_cookie_for", return_value="SESSDATA=offline; bili_jct=offline"), patch.object(app.BilibiliUploader, "upload", side_effect=timed_out_upload):
                service._upload_worker(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "uncertain"
            assert db.get_task(task_id)["status"] == "waiting"
            db.update_task(task_id, status="queued")
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "upload", side_effect=AssertionError("duplicate POST")), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV1check", "state": -3}):
                service._upload_worker(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "processing" and calls == ["submit"]
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV1check", "state": -30, "state_desc": "审核中", "is_only_self": 1}):
                service._refresh_upload_receipt(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "processing" and db.get_task(task_id)["status"] == "waiting"
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV1check", "state": -50, "is_only_self": 1}):
                service._refresh_upload_receipt(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "success"
            assert db.get_task(task_id)["status"] == "complete" and db.clip_review(cid)[0] == "published"
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV1check", "state": 0}):
                service._refresh_upload_receipt(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "visibility_mismatch"
            with patch.object(service, "_cookie_for", return_value="offline"), patch.object(app.BilibiliUploader, "find_receipt", return_value={"bvid": "BV1check", "state": -2, "state_desc": "审核未通过"}):
                service._refresh_upload_receipt(upload_id, task_id)
            assert db.get_upload(upload_id)["status"] == "rejected"
            with db._connect() as connection:
                connection.execute("UPDATE uploads SET metadata_json='not-json' WHERE id=?", (upload_id,))
            db.update_task(task_id, status="queued")
            with patch.object(app.BilibiliUploader, "upload", side_effect=AssertionError("invalid metadata resubmitted")):
                service._upload_worker(upload_id, task_id)
            assert db.get_task(task_id)["status"] == "error" and db.get_upload(upload_id)["bvid"] == "BV1check"
            uploader = app.BilibiliUploader(lambda: "offline")
            with patch.object(uploader, "_json", return_value={"code": 0, "data": {"arc_audits": [{"Archive": {"bvid": "BV1check", "state": -3}, "Videos": [{"cid": 1234}]}]}}):
                assert uploader.find_receipt(cid=1234)["bvid"] == "BV1check"
            renderer = app.FFmpeg(app.replace(settings, llm_model="test-vision"))

            def frame_command(args, _timeout):
                picture = app.Image.new("RGB", (640, 360), "#334455")
                app.ImageDraw.Draw(picture).rectangle((200, 50, 440, 330), fill="#DDCCAA")
                picture.save(args[-1])
                return 0, "", ""

            with patch.object(renderer, "_run", side_effect=frame_command), patch.object(app.LLMClient, "vision", return_value='{"approved":true,"frame_index":2,"reason":"主体清楚，事件有回应"}'):
                chosen = renderer.review_cover(source, source, 0, 60, {"source": "llm", "representative_timestamp": 20}, lambda _: None)
            assert chosen["representative_timestamp"] == 3 and chosen["visual_review"]["method"] == "vision"
            assert chosen["visual_review"]["model"] == "test-vision"
            with patch.object(renderer, "_run", side_effect=frame_command), patch.object(app.LLMClient, "vision", return_value='{"approved":false,"frame_index":1,"reason":"标题夸大"}'):
                rejected = renderer.review_cover(source, source, 0, 60, {"source": "llm"}, lambda _: None)
            assert rejected["visual_review"]["approved"] is False
            assert "标题夸大" in app.visual_review_warning(rejected)
            for invalid in ('{"approved":"false","reason":"格式错误"}', '{"approved":false}', '{"approved":true,"frame_index":0,"reason":"编号无效"}'):
                with patch.object(renderer, "_run", side_effect=frame_command), patch.object(app.LLMClient, "vision", return_value=invalid):
                    try:
                        renderer.review_cover(source, source, 0, 60, {"source": "llm"}, lambda _: None)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("invalid review result was accepted")
            llm = app.LLMClient(settings)
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "完整"}}]},
                {"choices": [{"index": 0, "delta": {"content": "结果", "tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "search", "arguments": "{\"q\":"}}]}}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"测试\"}"}}]}, "finish_reason": "tool_calls"}]},
            ]
            stream = b"".join(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n"
            reply = llm._read_stream(io.BytesIO(stream))["choices"][0]["message"]
            assert reply["content"] == "完整结果" and json.loads(reply["tool_calls"][0]["function"]["arguments"]) == {"q": "测试"}
            try:
                llm._read_stream(io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'))
            except RuntimeError as exc:
                assert "中断" in str(exc)
            else:
                raise AssertionError("truncated stream accepted")
            with patch.object(app.urllib.request, "build_opener") as direct, patch.object(app.urllib.request, "urlopen") as system:
                app.DashScopeTranscriber._open_request(app.urllib.request.Request("https://dashscope.aliyuncs.com/test"), 15)
                assert direct.call_count == 1 and system.call_count == 0
                app.DashScopeTranscriber._open_request(app.urllib.request.Request("https://custom.example/test"), 15)
                assert system.call_count == 1
            with patch.object(llm, "_generate", return_value={"content": "ok"}) as generate:
                assert llm.vision("review", cover) == "ok"
                content = generate.call_args.args[0][0]["content"]
                assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            retry_client = app.LLMClient(app.replace(settings, llm_model="retry-check"))
            def unavailable(request, **_kwargs):
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO(b'{"error":"temporary"}'))
            with patch.object(app.urllib.request, "urlopen", side_effect=unavailable) as request, patch.object(app.time, "sleep"):
                try:
                    retry_client._generate([{"role": "user", "content": "check"}], "", [])
                except RuntimeError as exc:
                    assert "503" in str(exc) and request.call_count == 3
                else:
                    raise AssertionError("persistent 503 was accepted")
        finally:
            service.executor.shutdown(wait=True)
    print("Workflow repairs passed: timezone, ASR failure, cover requirement, concurrent deduplication, uncertain POST recovery, platform states and visual review")


def check_desktop() -> None:
    with tempfile.TemporaryDirectory(prefix="workflow-ui-") as folder:
        root_path = Path(folder)
        app.Settings(base_dir=str(root_path / "data"), auto_slice=False).save(root_path / "data/config.json")
        root = app.Tk()
        root.attributes("-alpha", 0.0)
        with patch.object(app, "runtime_root", return_value=root_path), patch.object(app.RecorderService, "start"):
            desktop = app.DesktopApp(root)
            try:
                desktop.db.add_room("1", "界面测试主播")
                video = root_path / "sample.mp4"
                cover = video.with_suffix(".jpg")
                app.Image.new("RGB", (1280, 720), "#445566").save(cover)
                rid = desktop.db.create_recording("1", "ui", "界面测试录播", str(video), app.now_text())
                cid = desktop.db.create_clip(rid, "一次完整事件的标题", 0, 90, str(video), metadata={"reason": "铺垫、反应和结果", "visual_review": {"reason": "主体清楚"}}, review_status="ready")
                desktop.db.set_clip_status(cid, "complete", thumbnail_path=str(cover))
                needs_review = desktop.db.create_clip(rid, "推荐较低但仍然存在的成片", 100, 130, str(video), metadata={"source": "llm"}, review_status="rejected", review_flags=["editorial_required"])
                legacy = desktop.db.create_clip(rid, "旧规则摘句", 140, 170, str(video), metadata={"source": "heuristic"}, review_status="rejected")
                for clip_id in (needs_review, legacy):
                    desktop.db.set_clip_status(clip_id, "complete", thumbnail_path=str(cover))
                desktop._refresh_all()
                root.update()
                assert desktop.clip_tree.exists(str(needs_review)) and not desktop.clip_tree.exists(str(legacy))
                assert desktop.clip_tree.item(str(needs_review), "values")[-1] == "需复核"
                assert desktop.dashboard_clip_tree.exists(str(needs_review)) and not desktop.dashboard_clip_tree.exists(str(legacy))
                assert "2 个成片" in desktop.dashboard_status_var.get()
                assert desktop.db.clip_review(needs_review)[0] == "rejected", "visibility must not change publishing eligibility"
                assert not hasattr(desktop, "show_legacy_clips_var")
                desktop._refresh_clips()
                assert not desktop.clip_tree.exists(str(legacy))
                assert desktop.db.get_clip(legacy), "hidden historical candidates must remain in the database"
                for metadata in ("broken-json", "[]", "null", "{}"):
                    assert not app.is_legacy_heuristic_clip({"metadata_json": metadata}), "missing metadata must not make a real clip disappear"
                assert not app.is_legacy_heuristic_clip({"metadata_json": '{"source":"heuristic"}', "review_status": "published"}), "a published clip must remain visible even if it originated as a rule candidate"
                assert str(desktop.notebook["style"]) == "Workspace.TNotebook"
                assert len(desktop.navigation_tree.get_children("production")) == 5
                assert len(desktop.navigation_tree.get_children("manage")) == 3
                for width, height in ((1280, 820), (1020, 680)):
                    root.geometry(f"{width}x{height}")
                    desktop.navigation_tree.selection_set("切片")
                    desktop.navigation_tree.event_generate("<<TreeviewSelect>>")
                    root.update()
                    desktop.clip_tree.selection_set(str(cid))
                    desktop._on_clip_selected()
                    root.update()
                    assert desktop.notebook.tab(desktop.notebook.select(), "text") == "切片"
                    assert desktop.clip_preview_label["image"]
                    assert "主体清楚" in desktop.clip_evidence_text.get("1.0", "end")
                    preview = desktop.clip_preview_label.master
                    for child in preview.winfo_children():
                        if child.winfo_ismapped():
                            assert child.winfo_width() <= preview.winfo_width(), (width, str(child))
                    desktop._select_notebook_tab("设置")
                    desktop._settings_category_list.selection_set("media")
                    desktop._settings_category_list.event_generate("<<TreeviewSelect>>")
                    root.update()
                    assert not desktop._media_details.winfo_ismapped()
                    checkbox = next(child for child in desktop._media_details.master.winfo_children() if isinstance(child, app.ttk.Checkbutton))
                    checkbox.invoke()
                    root.update()
                    assert desktop._media_details.winfo_ismapped()
                    checkbox.invoke()
                desktop.room_tree.selection_set("1")
                with patch.object(app.simpledialog, "askstring", side_effect=AssertionError("sequential dialogs returned")):
                    desktop._edit_room()
                root.update()
                desktop._room_editor_vars["name"].set("更新后的主播")
                widgets = [desktop._room_editor]
                for widget in widgets:
                    widgets.extend(widget.winfo_children())
                save = next(widget for widget in widgets if isinstance(widget, app.ttk.Button) and str(widget["text"]) == "保存主播配置")
                assert save.winfo_rooty() + save.winfo_height() <= desktop._room_editor.winfo_rooty() + desktop._room_editor.winfo_height()
                save.invoke()
                assert desktop.db.get_room("1")["name"] == "更新后的主播"
                before = app.asdict(desktop.settings)
                desktop.setting_vars["subtitle_color"].set("not-a-color")
                with patch.object(app.messagebox, "showerror") as error:
                    desktop._save_settings()
                    error.assert_called_once()
                assert app.asdict(desktop.settings) == before
            finally:
                desktop.service.executor.shutdown(wait=True)
                app.logging.shutdown()
                root.destroy()
    print("Workflow desktop passed: grouped navigation, real cover preview, collapsed settings, one-form room editor and atomic settings validation")


if __name__ == "__main__":
    run()
