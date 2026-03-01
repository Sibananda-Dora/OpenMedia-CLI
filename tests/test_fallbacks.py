import unittest
from unittest.mock import patch

from openmedia.cli_frontend import (
    _extract_output_name_from_query,
    _audit_command_for_execution,
    _build_deterministic_fallback_command,
)
from openmedia.state import AgentState


class DeterministicFallbackTests(unittest.TestCase):
    def test_extract_output_name_supports_spaces(self):
        query = "Compress clip.mp4 for sharing and save as my final output.mp4."
        name = _extract_output_name_from_query(query)
        self.assertEqual(name, "my final output.mp4")

    def test_extract_output_name_supports_quoted_filename(self):
        query = 'Convert input.mov to MP4 and save as "project export v2.mp4".'
        name = _extract_output_name_from_query(query)
        self.assertEqual(name, "project export v2.mp4")

    def test_webp_lossless_fallback(self):
        query = (
            "Convert sample.gif to Lossless (Perfect Quality) WebP format for web optimization. "
            "Save as sample_optimized.webp."
        )
        cmd = _build_deterministic_fallback_command(query, "sample.gif", "nvidia")
        self.assertIsNotNone(cmd)
        self.assertIn("-lossless 1", cmd)
        self.assertIn("sample_optimized.webp", cmd)

    def test_extract_audio_fallback(self):
        query = (
            "Extract audio only from input.mp4 and save as input_audio.mp3. "
            "Keep clear listening quality."
        )
        cmd = _build_deterministic_fallback_command(query, "input.mp4", "cpu")
        self.assertIsNotNone(cmd)
        self.assertIn("-vn", cmd)
        self.assertIn("libmp3lame", cmd)

    def test_remove_audio_fallback(self):
        query = "Remove audio from input.mp4 while keeping video quality. Save as input_muted.mp4."
        cmd = _build_deterministic_fallback_command(query, "input.mp4", "cpu")
        self.assertIsNotNone(cmd)
        self.assertIn("-an", cmd)
        self.assertIn("input_muted.mp4", cmd)

    def test_video_compress_fallback_uses_nvenc_in_nvidia_mode(self):
        query = (
            "Compress input.mp4 for sharing using balanced compression. "
            "Scale to 720p. Use 30 fps. "
            "Preserve clear audio and save as input_share.mp4."
        )
        cmd = _build_deterministic_fallback_command(query, "input.mp4", "nvidia")
        self.assertIsNotNone(cmd)
        self.assertIn("h264_nvenc", cmd)
        self.assertIn("scale=-2:720", cmd)
        self.assertIn("fps=30", cmd)

    def test_change_format_audio_fallback(self):
        query = "Convert input.wav to MP3 with good quality. Save as input_converted.mp3."
        cmd = _build_deterministic_fallback_command(query, "input.wav", "cpu")
        self.assertIsNotNone(cmd)
        self.assertIn("libmp3lame", cmd)
        self.assertIn("input_converted.mp3", cmd)

    def test_change_format_image_fallback_returns_none(self):
        query = "Convert input.mp4 to PNG. Save as frame capture.png."
        cmd = _build_deterministic_fallback_command(query, "input.mp4", "cpu")
        self.assertIsNone(cmd)

    @patch("openmedia.cli_frontend.safety_reviewer_node")
    @patch("openmedia.cli_frontend.validator_node")
    def test_audit_command_short_circuits_when_validator_rejects(
        self, mock_validator, mock_reviewer
    ):
        rejected = AgentState(
            user_input="convert",
            target_file="input.mp4",
            generated_command="ffmpeg -i input.mp4 out.mp4",
            is_valid=False,
            error_message="Unsafe command",
        )
        mock_validator.return_value = rejected

        out = _audit_command_for_execution(
            "ffmpeg -i input.mp4 out.mp4",
            "convert",
            "input.mp4",
            "container=mp4",
            "cpu",
        )
        self.assertFalse(out.is_valid)
        mock_reviewer.assert_not_called()

    @patch("openmedia.cli_frontend.safety_reviewer_node")
    @patch("openmedia.cli_frontend.validator_node")
    def test_audit_command_runs_reviewer_after_validator(self, mock_validator, mock_reviewer):
        validated = AgentState(
            user_input="convert",
            target_file="input.mp4",
            generated_command="ffmpeg -i input.mp4 out.mp4",
            is_valid=True,
        )
        reviewed = AgentState(
            user_input="convert",
            target_file="input.mp4",
            generated_command="ffmpeg -i input.mp4 out.mp4",
            is_valid=True,
        )
        mock_validator.return_value = validated
        mock_reviewer.return_value = reviewed

        out = _audit_command_for_execution(
            "ffmpeg -i input.mp4 out.mp4",
            "convert",
            "input.mp4",
            "container=mp4",
            "cpu",
        )
        self.assertTrue(out.is_valid)
        mock_validator.assert_called_once()
        mock_reviewer.assert_called_once_with(validated)


if __name__ == "__main__":
    unittest.main()
