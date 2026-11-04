"""GRPO infrastructure: CPU reward, offline rehearsal, lazy Modal CUDA backend.

Group Relative Policy Optimization compares several plans for the same prompt,
using their normalized rewards as policy-gradient advantages (DeepSeekMath §4,
https://arxiv.org/abs/2402.03300). The checker gates reward; rollout and training
probabilities share the feasibility mask. Importing this package allocates no
accelerator and contacts no service.
"""

RL_VERSION = "stowlm-grpo@2.0"
