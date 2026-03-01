import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openmedia.executor import prepare_command_for_safe_execution
from openmedia.nodes import safety_reviewer_node, validator_node
from openmedia.state import AgentState


class SafetyPipelineTests(unittest.TestCase):
    def test_cpu_tuning_applies_defaults(self):
        command = "ffmpeg -i input.mp4 output_file.mp4"
        tuned_command, changed = prepare_command_for_safe_execution(
            command, "cpu", "input.mp4"
        )

        self.assertTrue(changed)
        tokens = shlex.split(tuned_command, posix=False)

        self.assertIn("-threads", tokens)
        self.assertIn("-c:v", tokens)
        self.assertIn("-preset", tokens)
        self.assertIn("-crf", tokens)
        self.assertEqual(tokens[tokens.index("-c:v") + 1], "libx264")
        self.assertEqual(tokens[tokens.index("-preset") + 1], "veryfast")
        self.assertEqual(tokens[tokens.index("-crf") + 1], "23")
        self.assertTrue(tokens[-1].endswith("_openmedia.mp4"))

    def test_output_collision_gets_unique_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            input_file = tmp / "input.mp4"
            output_file = tmp / "result.mp4"
            input_file.touch()
            output_file.touch()

            command = f'ffmpeg -i "{input_file}" "{output_file}"'
            tuned_command, _ = prepare_command_for_safe_execution(
                command, "cpu", str(input_file)
            )
            tokens = shlex.split(tuned_command, posix=False)
            final_output = Path(tokens[-1])

            self.assertNotEqual(final_output, output_file)
            self.assertTrue(final_output.name.startswith("result_"))
            self.assertFalse(final_output.exists())

    def test_ultra_safe_reduces_threads_and_uses_faster_cpu_preset(self):
        command = "ffmpeg -i input.mp4 output_file.mp4"

        normal_cmd, _ = prepare_command_for_safe_execution(command, "cpu", "input.mp4")
        ultra_cmd, _ = prepare_command_for_safe_execution(
            command, "cpu", "input.mp4", ultra_safe=True
        )

        normal_tokens = shlex.split(normal_cmd, posix=False)
        ultra_tokens = shlex.split(ultra_cmd, posix=False)

        normal_threads = int(normal_tokens[normal_tokens.index("-threads") + 1])
        ultra_threads = int(ultra_tokens[ultra_tokens.index("-threads") + 1])

        self.assertLessEqual(ultra_threads, normal_threads)
        self.assertEqual(ultra_tokens[ultra_tokens.index("-preset") + 1], "superfast")
        self.assertEqual(ultra_tokens[ultra_tokens.index("-crf") + 1], "25")

    def test_validator_blocks_cpu_heavy_preset(self):
        state = AgentState(
            user_input="compress",
            target_file="input.mp4",
            effective_encoder_family="cpu",
            generated_command="ffmpeg -i input.mp4 -preset veryslow out.mp4",
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("too heavy", out.error_message)

    def test_nvidia_tuning_rewrites_x264_style_flags(self):
        command = (
            "ffmpeg -i input.mp4 -c:v h264_nvenc -preset veryfast -crf 28 out.mp4"
        )
        tuned_command, changed = prepare_command_for_safe_execution(
            command, "nvidia", "input.mp4"
        )
        self.assertTrue(changed)
        tokens = shlex.split(tuned_command, posix=False)
        self.assertIn("-c:v", tokens)
        self.assertEqual(tokens[tokens.index("-c:v") + 1], "h264_nvenc")
        self.assertIn("-preset", tokens)
        self.assertEqual(tokens[tokens.index("-preset") + 1], "fast")
        self.assertNotIn("-crf", tokens)
        self.assertIn("-cq", tokens)
        self.assertEqual(tokens[tokens.index("-cq") + 1], "28")

    def test_nvidia_tuning_does_not_force_nvenc_for_gif_output(self):
        command = (
            'ffmpeg -i input.mp4 -filter_complex '
            '"fps=15,scale=480:-1:flags=lanczos,split[s0][s1];'
            '[s0]palettegen[p];[s1][p]paletteuse" out.gif'
        )
        tuned_command, changed = prepare_command_for_safe_execution(
            command, "nvidia", "input.mp4"
        )
        tokens = shlex.split(tuned_command, posix=False)

        self.assertNotIn("h264_nvenc", [t.lower() for t in tokens])
        self.assertNotIn("-cq", tokens)
        self.assertNotIn("-crf", tokens)
        self.assertIn("out.gif", tuned_command)

    def test_validator_blocks_nvidia_crf(self):
        state = AgentState(
            user_input="compress",
            target_file="input.mp4",
            effective_encoder_family="nvidia",
            generated_command="ffmpeg -i input.mp4 -c:v h264_nvenc -crf 28 out.mp4",
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("'-cq' instead of '-crf'", out.error_message)

    def test_validator_blocks_embedded_shell_operators(self):
        for cmd in [
            "ffmpeg -i input.mp4 out.mp4&del input.mp4",
            "ffmpeg -i input.mp4 out.mp4;del input.mp4",
            "ffmpeg -i input.mp4 out.mp4>nul",
            "ffmpeg -i input.mp4 out.mp4|more",
        ]:
            state = AgentState(
                user_input="convert",
                target_file="input.mp4",
                effective_encoder_family="cpu",
                generated_command=cmd,
            )
            out = validator_node(state)
            self.assertFalse(out.is_valid)
            self.assertIn("Unsafe format", out.error_message)

    def test_validator_allows_gif_without_nvenc_in_nvidia_mode(self):
        state = AgentState(
            user_input="make gif",
            target_file="input.mp4",
            effective_encoder_family="nvidia",
            generated_command=(
                'ffmpeg -i input.mp4 -filter_complex '
                '"fps=15,scale=480:-1:flags=lanczos,split[s0][s1];'
                '[s0]palettegen[p];[s1][p]paletteuse" out.gif'
            ),
        )
        out = validator_node(state)
        self.assertTrue(out.is_valid)

    def test_validator_allows_webp_with_libwebp_in_nvidia_mode(self):
        state = AgentState(
            user_input="convert to webp",
            target_file="input.gif",
            effective_encoder_family="nvidia",
            generated_command=(
                "ffmpeg -i input.gif -c:v libwebp -lossless 1 out.webp"
            ),
        )
        out = validator_node(state)
        self.assertTrue(out.is_valid)

    def test_validator_rejects_gif_with_multiple_inputs(self):
        state = AgentState(
            user_input="make gif",
            target_file="input.mp4",
            effective_encoder_family="nvidia",
            generated_command=(
                'ffmpeg -i input.mp4 -vf "fps=30,scale=720:-1:flags=lanczos,palettegen" '
                'palette.png -i input.mp4 -i palette.png '
                '-filter_complex "[0:v]fps=30,scale=720:-1:flags=lanczos[p];[p][1:v]paletteuse" out.gif'
            ),
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("GIF policy violation", out.error_message)

    def test_validator_rejects_gif_palettegen_without_paletteuse(self):
        state = AgentState(
            user_input="make gif",
            target_file="input.mp4",
            effective_encoder_family="nvidia",
            generated_command='ffmpeg -i input.mp4 -vf "fps=30,scale=720:-1,palettegen" out.gif',
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("palettegen must be paired with paletteuse", out.error_message)

    def test_validator_blocks_shell_chaining(self):
        state = AgentState(
            user_input="convert",
            target_file="input.mp4",
            effective_encoder_family="cpu",
            generated_command="ffmpeg -i input.mp4 out.mp4 && del input.mp4",
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("shell", out.error_message.lower())

    def test_validator_rejects_input_output_path_collision(self):
        state = AgentState(
            user_input="convert",
            target_file="input.mp4",
            effective_encoder_family="cpu",
            generated_command="ffmpeg -i input.mp4 input.mp4",
        )
        out = validator_node(state)
        self.assertFalse(out.is_valid)
        self.assertIn("input and output paths must be different", out.error_message.lower())

    @patch("openmedia.nodes.requests.post")
    def test_safety_reviewer_requires_exact_approved(self, mock_post):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"response": "APPROVED but avoid this command"}
        mock_post.return_value = mock_response

        state = AgentState(
            user_input="convert",
            target_file="input.mp4",
            media_context="container=mp4",
            effective_encoder_family="cpu",
            generated_command="ffmpeg -i input.mp4 out.mp4",
            is_valid=True,
        )
        out = safety_reviewer_node(state)
        self.assertFalse(out.is_valid)
        self.assertIsNone(out.generated_command)
        self.assertIn("Security Audit Failed", out.error_message)

    @patch("openmedia.nodes.requests.post")
    def test_safety_reviewer_accepts_exact_approved(self, mock_post):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"response": "APPROVED"}
        mock_post.return_value = mock_response

        state = AgentState(
            user_input="convert",
            target_file="input.mp4",
            media_context="container=mp4",
            effective_encoder_family="cpu",
            generated_command="ffmpeg -i input.mp4 out.mp4",
            is_valid=True,
        )
        out = safety_reviewer_node(state)
        self.assertTrue(out.is_valid)
        self.assertEqual(out.generated_command, "ffmpeg -i input.mp4 out.mp4")


if __name__ == "__main__":
    unittest.main()
