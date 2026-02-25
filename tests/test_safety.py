import shlex
import tempfile
import unittest
from pathlib import Path

from openmedia.executor import prepare_command_for_safe_execution
from openmedia.nodes import validator_node
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


if __name__ == "__main__":
    unittest.main()
