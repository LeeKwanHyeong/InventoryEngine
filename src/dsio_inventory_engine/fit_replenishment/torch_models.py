"""Optional CPU/float64 training backend. Never imported by inference."""

import torch
from torch import nn


class QuantileModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(4, 1, dtype=torch.float64, device="cpu")

    def forward(self, x):
        return torch.nn.functional.softplus(self.linear(x)).squeeze(-1)


class ActorCritic(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(14, 16, dtype=torch.float64, device="cpu")
        self.fc2 = nn.Linear(16, 16, dtype=torch.float64, device="cpu")
        self.actor = nn.Linear(16, 4, dtype=torch.float64, device="cpu")
        self.critic = nn.Linear(16, 1, dtype=torch.float64, device="cpu")

    def forward(self, x):
        hidden = torch.tanh(self.fc2(torch.tanh(self.fc1(x))))
        return self.actor(hidden), self.critic(hidden).squeeze(-1)


def pinball(prediction, target, quantile):
    error = target - prediction
    return torch.maximum(quantile * error, (quantile - 1) * error).mean()


def clipped_objective(ratio, advantage, epsilon=0.2):
    return torch.minimum(ratio * advantage, ratio.clamp(1 - epsilon, 1 + epsilon) * advantage)


def advantages(
    rewards: list[float], values: list[float], gamma=0.99, lam=0.95
) -> tuple[list[float], list[float]]:
    # Finite planning episode with explicit terminal cost, NOT a continuing-task timeout.
    result = [0.0] * len(rewards)
    after = 0.0
    next_value = 0.0
    for index in reversed(range(len(rewards))):
        delta = rewards[index] + gamma * next_value - values[index]
        after = delta + gamma * lam * after
        result[index] = after
        next_value = values[index]
    return result, [a + v for a, v in zip(result, values, strict=True)]


def weights(model) -> dict:
    def convert(value):
        return [convert(v) for v in value] if isinstance(value, list) else format(value, ".17g")

    return {
        name: convert(tensor.detach().cpu().tolist()) for name, tensor in model.state_dict().items()
    }
