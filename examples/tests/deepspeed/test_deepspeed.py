# Copyright 2020 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import unittest

from transformers.integrations import is_deepspeed_available
from transformers.testing_utils import (
    TestCasePlus,
    execute_subprocess_async,
    get_gpu_count,
    require_torch_gpu,
    require_torch_multi_gpu,
    slow,
)
from transformers.trainer_utils import set_seed


set_seed(42)
MBART_TINY = "sshleifer/tiny-mbart"


def load_json(path):
    with open(path) as f:
        return json.load(f)


# a candidate for testing_utils
def require_deepspeed(test_case):
    """
    Decorator marking a test that requires deepspeed
    """
    if not is_deepspeed_available():
        return unittest.skip("test requires deepspeed")(test_case)
    else:
        return test_case


@slow
@require_deepspeed
@require_torch_gpu
class TestDeepSpeed(TestCasePlus):
    @require_torch_multi_gpu
    def test_basic_distributed(self):
        self.run_quick(distributed=True)

    @require_torch_multi_gpu
    def test_grad_acum(self):
        self.run_quick(distributed=True, extra_args_str="--gradient_accumulation_steps 2")

    def test_do_eval_no_train(self):
        # we should not fail if train is skipped
        output_dir = self.run_trainer(
            eval_steps=1,
            max_len=12,
            model_name=MBART_TINY,
            num_train_epochs=1,
            distributed=False,
            extra_args_str="--do_eval",
            remove_args_str="--do_train",
        )
        val_metrics = load_json(os.path.join(output_dir, "val_results.json"))
        assert "val_bleu" in val_metrics

    # XXX: need to do better validation beyond just that the run was successful
    def run_quick(self, distributed=True, extra_args_str=None, remove_args_str=None):
        output_dir = self.run_trainer(
            eval_steps=1,
            max_len=12,
            model_name=MBART_TINY,
            num_train_epochs=1,
            distributed=distributed,
            extra_args_str=extra_args_str,
            remove_args_str=remove_args_str,
        )
        train_metrics = load_json(os.path.join(output_dir, "train_results.json"))
        assert "train_runtime" in train_metrics

    def run_trainer(
        self,
        eval_steps: int,
        max_len: str,
        model_name: str,
        num_train_epochs: int,
        distributed: bool = True,
        extra_args_str: str = None,
        remove_args_str: str = None,
    ):
        data_dir = self.examples_dir / "seq2seq/test_data/wmt_en_ro"
        output_dir = self.get_auto_remove_tmp_dir()
        args = f"""
            --model_name_or_path {model_name}
            --data_dir {data_dir}
            --output_dir {output_dir}
            --overwrite_output_dir
            --n_train 8
            --n_val 8
            --max_source_length {max_len}
            --max_target_length {max_len}
            --val_max_target_length {max_len}
            --do_train
            --num_train_epochs {str(num_train_epochs)}
            --per_device_train_batch_size 4
            --learning_rate 3e-3
            --warmup_steps 8
            --predict_with_generate
            --logging_steps 0
            --save_steps {str(eval_steps)}
            --group_by_length
            --label_smoothing_factor 0.1
            --adafactor
            --task translation
            --tgt_lang ro_RO
            --src_lang en_XX
        """.split()

        if extra_args_str is not None:
            args.extend(extra_args_str.split())

        if remove_args_str is not None:
            remove_args = remove_args_str.split()
            args = [x for x in args if x not in remove_args]

        ds_args = f"--deepspeed {self.test_file_dir_str}/ds_config.json".split()
        script = [f"{self.examples_dir_str}/seq2seq/finetune_trainer.py"]
        num_gpus = get_gpu_count() if distributed else 1
        launcher = f"deepspeed --num_gpus {num_gpus}".split()

        cmd = launcher + script + args + ds_args
        # keep for quick debug
        # print(" ".join([f"PYTHONPATH={self.src_dir_str}"] +cmd)); die
        execute_subprocess_async(cmd, env=self.get_env())

        return output_dir
