"""贝叶斯优化 (TPE) 备选 —— 使用 Optuna"""

import numpy as np
from config.settings import BacktestConfig
from optimizer.objective import fitness, PARAM_NAMES


def run(
    start_date: str = "20240101",
    end_date: str = "20250601",
    n_trials: int = 100,
) -> tuple[dict, float]:
    """使用 Optuna TPE 采样器优化"""
    try:
        import optuna
    except ImportError:
        print("请先安装 optuna: pip install optuna")
        return {}, 0.0

    config = BacktestConfig(start_date=start_date, end_date=end_date)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    def objective_fn(trial):
        params = {}
        for name in PARAM_NAMES:
            params[name] = trial.suggest_float(name, 0.01, 0.60)
        # 分组归一化
        groups = [
            ["w1", "w2", "w3", "w4", "w5"],
            ["f1_s1", "f1_s2", "f1_s3", "f1_s4"],
            ["f2_s1", "f2_s2", "f2_s3", "f2_s4"],
            ["f3_s1", "f3_s2", "f3_s3", "f3_s4"],
            ["f4_s1", "f4_s2", "f4_s3", "f4_s4"],
            ["f5_s1", "f5_s2", "f5_s3", "f5_s4"],
        ]
        for group in groups:
            s = sum(params[g] for g in group)
            if s > 0:
                for g in group:
                    params[g] /= s
        return fitness(params, config)

    print(f"贝叶斯优化开始: trials={n_trials}")
    study.optimize(objective_fn, n_trials=n_trials)

    best_params = study.best_params
    best_score = study.best_value
    print(f"优化完成: best calmar = {best_score:.4f}")
    return best_params, best_score
