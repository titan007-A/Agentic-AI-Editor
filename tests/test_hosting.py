import httpx
import pytest

from agentic_editor import hosting

moto_server = pytest.importorskip("moto.server")


@pytest.fixture
def s3(monkeypatch):
    """An S3 stand-in on a local port (R2 speaks the S3 API), with a bucket; its objects are
    readable without signing, as through a bucket's public domain."""
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    config = hosting.R2("0123456789abcdef", "AKIDTEST", "secret", "reels",
                        "https://media.example.com", endpoint=endpoint)  # fmt: skip
    import json

    import boto3

    admin = boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id="AKIDTEST", aws_secret_access_key="secret",
    )  # fmt: skip
    admin.create_bucket(Bucket="reels")
    public = {"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
              "Resource": "arn:aws:s3:::reels/*"}  # fmt: skip
    admin.put_bucket_policy(
        Bucket="reels", Policy=json.dumps({"Version": "2012-10-17", "Statement": [public]})
    )
    real_get = httpx.get

    def public_get(url, **kwargs):
        # The public domain serves the bucket's objects: here, path-style from the stand-in.
        return real_get(url.replace("https://media.example.com", f"{endpoint}/reels"), **kwargs)

    monkeypatch.setattr(hosting.httpx, "get", public_get)
    yield config
    server.stop()


def test_a_video_is_put_in_the_bucket_with_its_type_and_cache_rule_then_deleted(s3, tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000)
    hosting.upload(s3, path, "reels/v.mp4")
    got = hosting.client(s3).get_object(Bucket="reels", Key="reels/v.mp4")
    assert got["Body"].read() == path.read_bytes()
    assert got["ContentType"] == "video/mp4" and got["CacheControl"] == hosting.CACHE
    assert s3.link("reels/v.mp4") == "https://media.example.com/reels/v.mp4"
    hosting.delete(s3, "reels/v.mp4")
    listed = hosting.client(s3).list_objects_v2(Bucket="reels")
    assert listed.get("KeyCount", 0) == 0


def test_the_check_puts_reads_back_and_deletes_a_small_file(s3):
    hosting.check(s3)
    listed = hosting.client(s3).list_objects_v2(Bucket="reels")
    assert listed.get("KeyCount", 0) == 0  # nothing left behind


def test_the_check_says_which_step_failed(s3, monkeypatch):
    wrong_bucket = hosting.R2(**{**vars(s3), "bucket": "nope"})
    with pytest.raises(hosting.HostingError, match="putting a file in the bucket failed"):
        hosting.check(wrong_bucket)
    with pytest.raises(hosting.HostingError, match="https"):
        hosting.check(hosting.R2(**{**vars(s3), "public_url": "http://media.example.com"}))
    # The public domain isn't the bucket's: whatever it answers, the file isn't there.
    monkeypatch.setattr(hosting.httpx, "get", lambda url, **k: httpx.Response(404))
    with pytest.raises(hosting.HostingError, match="Custom Domains"):
        hosting.check(s3)
    listed = hosting.client(s3).list_objects_v2(Bucket="reels")
    assert listed.get("KeyCount", 0) == 0


def test_settings_are_kept_and_read_back(tmp_path):
    from agentic_editor.db import make_sessionmaker

    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as session:
        assert hosting.load(session) is None and not hosting.usable(None)
        config = hosting.R2("abcdef0123456789", "AK", "SK", "b", "https://m.example.com")
        hosting.save(session, config)
        session.commit()
        assert hosting.load(session) == config and hosting.usable(config)
        assert config.api() == "https://abcdef0123456789.r2.cloudflarestorage.com"
        hosting.save(session, None)
        session.commit()
        assert hosting.load(session) is None
