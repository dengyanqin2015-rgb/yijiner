"""遗传算法优化器: 25个权重参数，分组独立归一化"""

import numpy as np
import random
from config.settings import BacktestConfig
from optimizer.objective import fitness

# 参数名列表 (25个)
PARAM_NAMES = [
    "w1", "w2", "w3", "w4", "w5",
    "f1_s1", "f1_s2", "f1_s3", "f1_s4",
    "f2_s1", "f2_s2", "f2_s3", "f2_s4",
    "f3_s1", "f3_s2", "f3_s3", "f3_s4",
    "f4_s1", "f4_s2", "f4_s3", "f4_s4",
    "f5_s1", "f5_s2", "f5_s3", "f5_s4",
]

# 参数分组 (用于独立归一化)
PARAM_GROUPS = [
    [0, 1, 2, 3, 4],           # 一级权重
    [5, 6, 7, 8],               # F1 子因子
    [9, 10, 11, 12],            # F2 子因子
    [13, 14, 15, 16],           # F3 子因子
    [17, 18, 19, 20],           # F4 子因子
    [21, 22, 23, 24],           # F5 子因子
]


def normalize_group(individual: np.ndarray) -> np.ndarray:
    """对每组参数独立归一化，使组内和为1"""
    for group in PARAM_GROUPS:
        group_sum = np.sum(individual[group])
        if group_sum > 0:
            individual[group] /= group_sum
    return individual


def random_individual() -> np.ndarray:
    """生成随机个体 (每维 0.01~0.60)"""
    ind = np.random.uniform(0.01, 0.60, 25)
    return normalize_group(ind)


def sbx_crossover(p1: np.ndarray, p2: np.ndarray, eta: float = 15.0) -> tuple[np.ndarray, np.ndarray]:
    """模拟二进制交叉"""
    c1, c2 = p1.copy(), p2.copy()
    for i in range(len(p1)):
        if random.random() < 0.8:
            u = random.random()
            if u <= 0.5:
                beta = (2 * u) ** (1 / (eta + 1))
            else:
                beta = (1 / (2 * (1 - u))) ** (1 / (eta + 1))
            c1[i] = 0.5 * ((1 + beta) * p1[i] + (1 - beta) * p2[i])
            c2[i] = 0.5 * ((1 - beta) * p1[i] + (1 + beta) * p2[i])
    c1 = normalize_group(c1)
    c2 = normalize_group(c2)
    return np.clip(c1, 0.01, 0.60), np.clip(c2, 0.01, 0.60)


def polynomial_mutation(ind: np.ndarray, eta: float = 20.0) -> np.ndarray:
    """多项式变异"""
    mutant = ind.copy()
    for i in range(len(mutant)):
        if random.random() < 0.15:
            u = random.random()
            delta = min(mutant[i] - 0.01, 0.60 - mutant[i])
            if u <= 0.5:
                dq = (2 * u) ** (1 / (eta + 1)) - 1
            else:
                dq = 1 - (2 * (1 - u)) ** (1 / (eta + 1))
            mutant[i] += dq * delta
    mutant = normalize_group(mutant)
    return np.clip(mutant, 0.01, 0.60)


def tournament_select(population: list, scores: list, tournament_size: int = 3) -> np.ndarray:
    """锦标赛选择"""
    candidates = random.sample(range(len(population)), tournament_size)
    best = max(candidates, key=lambda i: scores[i])
    return population[best].copy()


def run(
    start_date: str = "20240101",
    end_date: str = "20250601",
    pop_size: int = 50,
    generations: int = 30,
    elite_size: int = 5,
    patience: int = 5,
) -> tuple[dict, float]:
    """运行遗传算法优化"""
    config = BacktestConfig(start_date=start_date, end_date=end_date)

    # 初始化种群
    population = [random_individual() for _ in range(pop_size)]
    scores = [fitness(dict(zip(PARAM_NAMES, ind)), config) for ind in population]

    best_score = max(scores)
    best_ind = population[scores.index(best_score)].copy()
    no_improve = 0

    print(f"遗传算法优化开始: pop={pop_size}, gen={generations}")
    print(f"Gen 0: best fitness = {best_score:.4f}")

    for gen in range(1, generations + 1):
        # 精英保留
        elite_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:elite_size]
        new_pop = [population[i].copy() for i in elite_indices]

        # 生成新一代
        while len(new_pop) < pop_size:
            p1 = tournament_select(population, scores)
            p2 = tournament_select(population, scores)
            c1, c2 = sbx_crossover(p1, p2)
            c1 = polynomial_mutation(c1)
            c2 = polynomial_mutation(c2)
            new_pop.append(c1)
            if len(new_pop) < pop_size:
                new_pop.append(c2)

        population = new_pop
        scores = [fitness(dict(zip(PARAM_NAMES, ind)), config) for ind in population]

        gen_best = max(scores)
        if gen_best > best_score:
            best_score = gen_best
            best_ind = population[scores.index(gen_best)].copy()
            no_improve = 0
            print(f"Gen {gen}: *** new best fitness = {best_score:.4f} ***")
        else:
            no_improve += 1
            if gen % 5 == 0:
                print(f"Gen {gen}: best fitness = {gen_best:.4f} (no improve {no_improve})")

        if no_improve >= patience:
            print(f"Early stop at gen {gen}")
            break

    best_params = dict(zip(PARAM_NAMES, best_ind))
    print(f"\n优化完成: best calmar = {best_score:.4f}")
    return best_params, best_score
