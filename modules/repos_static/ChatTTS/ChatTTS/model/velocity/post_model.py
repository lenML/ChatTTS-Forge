import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"
"""
https://stackoverflow.com/questions/62691279/how-to-disable-tokenizers-parallelism-true-false-warning
"""

from typing import Callable, List

import torch
import torch.nn as nn
from torch.functional import F
from torch.nn.utils.parametrizations import weight_norm


class PostModel(nn.Module):
    def __init__(
        self, hidden_size: int, num_audio_tokens: int, num_text_tokens: int, num_vq=4
    ):
        super().__init__()

        self.num_vq = num_vq
        self.num_audio_tokens = num_audio_tokens

        self.model_dim = hidden_size
        self.emb_code = nn.ModuleList(
            [nn.Embedding(num_audio_tokens, self.model_dim) for _ in range(num_vq)],
        )
        self.emb_text = nn.Embedding(num_text_tokens, self.model_dim)

        self.head_text = weight_norm(
            nn.Linear(self.model_dim, num_text_tokens, bias=False),
            name="weight",
        )
        self.head_code = nn.ModuleList(
            [
                weight_norm(
                    nn.Linear(self.model_dim, num_audio_tokens, bias=False),
                    name="weight",
                )
                for _ in range(self.num_vq)
            ],
        )

    def forward(self, input_ids: torch.Tensor, text_mask: torch.Tensor) -> torch.Tensor:
        """
        get_emb
        """
        device = next(self.parameters()).device
        emb_text: torch.Tensor = self.emb_text(
            input_ids[text_mask].narrow(1, 0, 1).squeeze_(1).to(device)
        )

        text_mask_inv = text_mask.logical_not().to(device)
        masked_input_ids: torch.Tensor = input_ids[text_mask_inv].to(device)

        emb_code = [
            self.emb_code[i](masked_input_ids[:, i]) for i in range(self.num_vq)
        ]
        emb_code = torch.stack(emb_code, 2).sum(2)

        emb = torch.zeros(
            (input_ids.shape[:-1]) + (emb_text.shape[-1],),
            device=emb_text.device,
            dtype=emb_text.dtype,
        )
        emb[text_mask] = emb_text
        emb[text_mask_inv] = emb_code.to(emb.dtype)

        del emb_text, emb_code, text_mask_inv

        return emb


class Sampler:
    def __init__(self, post_model: PostModel, num_audio_tokens: int, num_vq: int):
        self.post_model = post_model
        self.device = next(self.post_model.parameters()).device
        self.num_audio_tokens = num_audio_tokens
        self.num_vq = num_vq

    def sample(
        self,
        inputs_ids: torch.Tensor,
        hidden_states: torch.Tensor,
        infer_text: bool = False,
        temperature: torch.Tensor = 1.0,
        logits_processors: List[Callable] = [
            lambda logits_token, logits: logits,
        ],
        logits_warpers: List[Callable] = [
            lambda logits_token, logits: logits,
        ],
        min_new_token: int = 0,
        now_length: int = 0,
        eos_token: int = 0,
        start_idx: int = 0,
    ):
        # print(inputs_ids.shape)
        B = hidden_states.shape[0]

        end_idx = torch.zeros(
            inputs_ids.shape[0], device=inputs_ids.device, dtype=torch.long
        )
        finish = torch.zeros(inputs_ids.shape[0], device=inputs_ids.device).bool()
        if not infer_text:
            temperature = (
                temperature.unsqueeze(0)
                .expand(inputs_ids.shape[0], -1)
                .contiguous()
                .view(-1, 1)
            )

        if infer_text:
            logits: torch.Tensor = self.post_model.head_text(hidden_states)
        else:
            # logits = torch.stack([self.head_code[i](hidden_states) for i in range(self.num_vq)], 3)
            logits = torch.empty(
                hidden_states.size(0),
                hidden_states.size(1),
                self.num_audio_tokens,
                self.num_vq,
                dtype=torch.float,
                device=self.device,
            )
            for num_vq_iter in range(self.num_vq):
                x: torch.Tensor = self.post_model.head_code[num_vq_iter](hidden_states)
                logits[..., num_vq_iter] = x
                del x

        del hidden_states

        # logits = logits[:, -1].float()
        logits = logits.narrow(1, -1, 1).squeeze_(1).float()

        if not infer_text:
            # logits = rearrange(logits, "b c n -> (b n) c")
            logits = logits.permute(0, 2, 1)
            logits = logits.reshape(-1, logits.size(2))
            # logits_token = rearrange(inputs_ids[:, start_idx:], "b c n -> (b n) c")
            inputs_ids_sliced = inputs_ids[:, start_idx:].permute(0, 2, 1)
            logits_token = inputs_ids_sliced.reshape(
                inputs_ids_sliced.size(0) * inputs_ids_sliced.size(1),
                -1,
            ).to(self.device)
        else:
            logits_token = inputs_ids[:, start_idx:, 0].to(self.device)

        logits /= temperature

        for logitsProcessors in logits_processors:
            logits = logitsProcessors(logits_token, logits)

        for logitsWarpers in logits_warpers:
            logits = logitsWarpers(logits_token, logits)

        del logits_token

        if now_length < min_new_token:
            logits[:, eos_token] = -torch.inf

        scores = F.softmax(logits, dim=-1)
        idx_next = torch.multinomial(scores, num_samples=1).to(finish.device)
        if not infer_text:
            scores = scores.reshape(B, -1, scores.shape[-1])
        if not infer_text:
            # idx_next = rearrange(idx_next, "(b n) 1 -> b n", n=self.num_vq)
            idx_next = idx_next.view(-1, self.num_vq)
            finish_or = idx_next.eq(eos_token).any(1)
            finish.logical_or_(finish_or)
            del finish_or
        else:
            finish_or = idx_next.eq(eos_token).any(1)
            finish.logical_or_(finish_or)
            del finish_or

        del inputs_ids

        not_finished = finish.logical_not().to(end_idx.device)

        end_idx.add_(not_finished.int())
        idx_next = idx_next[:, None, :]
        return (
            idx_next,
            torch.log(scores),
            finish,
        )
