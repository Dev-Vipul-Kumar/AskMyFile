import tempfile
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from shares.models import Share


@override_settings(DEBUG=True)
class LargeUploadTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.media_settings.enable()
        self.storage = FileSystemStorage(location=self.temp_dir.name)
        self.storage_patch = patch("shares.services._private_storage", self.storage)
        self.storage_patch.start()
        self.chunk_size_patch = patch("shares.views.LARGE_UPLOAD_CHUNK_SIZE", 4)
        self.chunk_size_patch.start()

        self.admin = User.objects.create_superuser("admin@example.com", "password123")
        self.client.force_login(self.admin)

    def tearDown(self):
        self.chunk_size_patch.stop()
        self.storage_patch.stop()
        self.media_settings.disable()
        self.temp_dir.cleanup()

    def test_large_upload_assembles_chunks_into_streamed_share(self):
        start = self.client.post(reverse("shares:large_upload_start"), {
            "ciphertext_size": "10",
            "salt_b64": "salt",
            "iv_b64": "iv",
            "otp_hash": "a" * 64,
            "share_type": "file",
            "original_filename": "large.bin",
            "algorithm_version": "AES-GCM-256-v2",
        })
        self.assertEqual(start.status_code, 200)
        ticket = start.json()["ticket"]

        for index, content in enumerate((b"abcd", b"efgh", b"ij")):
            response = self.client.post(reverse("shares:large_upload_chunk"), {
                "ticket": ticket,
                "chunk_index": str(index),
                "chunk_data": SimpleUploadedFile("chunk.bin", content),
            })
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])

        complete = self.client.post(reverse("shares:large_upload_complete"), {
            "ticket": ticket,
        })
        self.assertEqual(complete.status_code, 200)
        self.assertTrue(complete.json()["ok"])

        share = Share.objects.get(pk=complete.json()["share_id"])
        with self.storage.open(share.storage_key, "rb") as stored:
            self.assertEqual(stored.read(), b"abcdefghij")
        self.assertEqual(share.ciphertext_size, 10)

    def test_file_chunk_larger_than_request_data_limit_is_accepted(self):
        chunk_size = 3 * 1024 * 1024
        with patch("shares.views.LARGE_UPLOAD_CHUNK_SIZE", chunk_size):
            start = self.client.post(reverse("shares:large_upload_start"), {
                "ciphertext_size": str(chunk_size + 1),
                "salt_b64": "salt",
                "iv_b64": "iv",
                "otp_hash": "b" * 64,
                "share_type": "file",
            })
            self.assertEqual(start.status_code, 200)

            response = self.client.post(reverse("shares:large_upload_chunk"), {
                "ticket": start.json()["ticket"],
                "chunk_index": "0",
                "chunk_data": SimpleUploadedFile("chunk.bin", b"x" * chunk_size),
            })
        self.assertEqual(response.status_code, 200)

        with patch("shares.views.LARGE_UPLOAD_CHUNK_SIZE", chunk_size):
            final_chunk = self.client.post(reverse("shares:large_upload_chunk"), {
                "ticket": start.json()["ticket"],
                "chunk_index": "1",
                "chunk_data": SimpleUploadedFile("chunk.bin", b"y"),
            })
            self.assertEqual(final_chunk.status_code, 200)
            complete = self.client.post(reverse("shares:large_upload_complete"), {
                "ticket": start.json()["ticket"],
            })
        self.assertEqual(complete.status_code, 200)
