import cvxpy as cp
import numpy as np
import pytest

import dsp
from dsp import (
    LocalVariable,
    MinimizeMaximize,
    SaddlePointProblem,
    saddle_inner,
    saddle_min,
    saddle_quad_form,
)


@pytest.mark.skipif(cp.MOSEK not in cp.installed_solvers(), reason="MOSEK not installed")
def test_robust_bond():
    C = np.loadtxt("tests/example_data/robust_bond_portfolio/C.csv")
    p = np.loadtxt("tests/example_data/robust_bond_portfolio/p.csv")
    w_bar = np.loadtxt("tests/example_data/robust_bond_portfolio/target_weights.csv")
    y_nominal = np.loadtxt("tests/example_data/robust_bond_portfolio/y_nominal.csv")
    h_mkt = (w_bar * 100) / p

    # Constants and parameters
    n, T = C.shape
    delta_max, kappa, omega = 0.02, 0.9, 1e-6
    B = 100
    V_lim = 90

    # Creating variables
    h = cp.Variable(n, nonneg=True)

    delta = LocalVariable(T)
    y = y_nominal + delta
    # TODO: investigate why
    # y = LocalVariable(T)
    # delta = y - y_nominal
    # breaks

    # Objective
    phi = 0.5 * cp.norm1(cp.multiply(h, p) - cp.multiply(h_mkt, p))

    # Creating saddle min function
    V = 0
    for i in range(n):
        t_plus_1 = np.arange(T) + 1  # Account for zero-indexing
        V += saddle_inner(cp.exp(cp.multiply(-t_plus_1, y)), h[i] * C[i])

    Y = [
        cp.norm_inf(delta) <= delta_max,
        cp.norm1(delta) <= kappa,
        cp.sum_squares(delta[1:] - delta[:-1]) <= omega,
    ]

    V_wc = saddle_min(V, Y)

    # Creating and solving the problem
    problem = cp.Problem(cp.Minimize(phi), [h @ p == B, V_wc >= V_lim])
    problem.solve()  # 15.31

    assert problem.status == cp.OPTIMAL

    plotting = False

    if plotting:
        import matplotlib.pyplot as plt
        import pandas as pd

        # added tentative plotting, please review
        inds = (
            ((C > 0).astype(int) * np.arange(C.shape[1])).argmax(axis=1).argsort()
        )  # sort by maturity
        df = pd.DataFrame({"h": h.value[inds], "h_mkt": (h_mkt)[inds]})
        df.plot(kind="bar")
        plt.xlabel("Bond index (increasing maturity)")
        plt.ylabel("Holdings")
        plt.savefig("tests/example_data/robust_bond.pdf")
        # plt.show()

        df = pd.DataFrame({"y_nom": y_nominal, "y_wc": y.value})
        df.plot()
        plt.xlabel(r"$t$")
        plt.ylabel("yield")
        plt.savefig("tests/example_data/yield.pdf")
        # plt.show()


def test_robust_markowitz():
    returns = np.loadtxt("tests/example_data/robust_portfolio_selection/ff_data.csv", delimiter=",")

    mu = np.mean(returns, axis=0)
    Sigma = np.cov(returns, rowvar=False)

    # Constants and parameters
    n = len(mu)
    rho, eta, gamma = 0.2, 0.2, 1

    # Creating variables
    w = cp.Variable(n, nonneg=True)

    delta_loc = LocalVariable(n)
    Sigma_perturbed = LocalVariable((n, n), PSD=True)
    Delta_loc = LocalVariable((n, n))

    # Creating saddle min function
    f = w @ mu + saddle_inner(delta_loc, w) - gamma * saddle_quad_form(w, Sigma_perturbed)

    Sigma_diag = Sigma.diagonal()
    local_constraints = [
        cp.abs(delta_loc) <= rho,
        Sigma_perturbed == Sigma + Delta_loc,
        cp.abs(Delta_loc) <= eta * np.sqrt(np.outer(Sigma_diag, Sigma_diag)),
    ]

    G = saddle_min(f, local_constraints)

    # Creating and solving the problem
    problem = cp.Problem(cp.Maximize(G), [cp.sum(w) == 1])
    problem.solve(solver=cp.SCS)  # 0.076

    nominal_objective = cp.Maximize(w @ mu - gamma * cp.quad_form(w, Sigma))

    robust_wc_utility = G.value
    robust_non_wc_utility = nominal_objective.value

    assert problem.status == cp.OPTIMAL

    # Creating and solving the problem without robustness
    problem = cp.Problem(nominal_objective, [cp.sum(w) == 1])
    problem.solve(solver=cp.SCS)  # 0.065
    assert problem.status == cp.OPTIMAL

    nominal_wc_utility = G.value
    nominal_non_wc_utility = nominal_objective.value

    assert robust_wc_utility > nominal_wc_utility
    assert robust_non_wc_utility < nominal_non_wc_utility


def test_arbitrage():
    p = np.ones(2)

    V = np.array([[1.5, 0.5], [0.5, 1.5]])

    x = cp.Variable(2, name="x")

    # No arbitrage
    obj = cp.Minimize(p @ x)
    constraints = [V @ x >= 0]
    no_arb_problem = cp.Problem(obj, constraints)
    no_arb_problem.solve()
    assert np.isclose(no_arb_problem.value, 0)

    # Arbitrage
    p[0] = 0.1  # arb possible for p[0] < 1/3
    obj = cp.Minimize(p @ x)
    constraints = [V @ x >= 0]
    arb_problem = cp.Problem(obj, constraints)
    arb_problem.solve()
    assert arb_problem.status == cp.UNBOUNDED

    # Robust arbitrage - small perturbation
    p_tilde = cp.Variable(2, name="p_tilde")
    obj = dsp.MinimizeMaximize(dsp.inner(x, p_tilde))
    constraints = [V @ x >= 0, cp.abs(p_tilde - p) <= 0.1]
    saddle_problem = dsp.SaddlePointProblem(obj, constraints)
    with pytest.raises(AssertionError):
        saddle_problem.solve()

    # Robust arbitrage - large perturbation
    constraints = [V @ x >= 0, cp.abs(p_tilde - p) <= 1]
    saddle_problem = dsp.SaddlePointProblem(obj, constraints)
    saddle_problem.solve()
    assert saddle_problem.status == cp.OPTIMAL
    assert np.isclose(saddle_problem.value, 0)


@pytest.mark.skipif(cp.MOSEK not in cp.installed_solvers(), reason="MOSEK not installed")
def test_robust_model_fitting():
    # Load data
    data = np.loadtxt("tests/example_data/robust_model_fitting/data.csv", delimiter=",", skiprows=1)

    p = data[:, 0]
    p = (p - np.mean(p)) / np.std(p)

    A = data[:, 1:]
    A = (A - np.mean(A, axis=0)) / np.std(A, axis=0)
    A = np.hstack((np.ones((len(p), 1)), A))

    # Constants
    m, n = A.shape
    k = 0.1 * m
    eta = 0.1

    # Creating variables
    theta = cp.Variable(n)
    weights = cp.Variable(m, nonneg=True)

    # Defining the loss function and the weight constraints
    loss = cp.square(A @ theta - p)
    regularizer = eta * cp.norm1(theta)
    objective = MinimizeMaximize(saddle_inner(loss, weights) + regularizer)
    constraints = [cp.sum(weights) == k, weights <= 1]

    # Creating and solving the problem
    problem = SaddlePointProblem(objective, constraints)
    problem.solve()  # 692.13
    assert problem.status == cp.OPTIMAL

    robust_obj_robust_weights = problem.value

    # OLS problem
    ols_objective = cp.Minimize(cp.sum_squares(A @ theta - p) + regularizer)

    ols_obj_robust_weights = ols_objective.value

    problem = cp.Problem(ols_objective)
    problem.solve()
    assert problem.status == cp.OPTIMAL

    ols_obj_ols_weights = problem.value
    robust_obj_ols_weights = np.sum(np.sort(np.square(A @ theta.value - p))[-int(k) :])

    assert ols_obj_ols_weights < ols_obj_robust_weights
    assert robust_obj_robust_weights < robust_obj_ols_weights

    # Using sum_largest
    loss = cp.sum_largest(cp.square(A @ theta - p), k)
    objective = cp.Minimize(loss + regularizer)
    problem = cp.Problem(objective)
    problem.solve()
    assert problem.status == cp.OPTIMAL

    assert np.isclose(problem.value, robust_obj_robust_weights)


def test_logistic():
    import pandas as pd

    df = pd.read_csv("http://bit.ly/bio304-titanic-data")

    def sigmoid(z):
        return 1 / (1 + np.exp(-z))

    df["sex"] = df["sex"] == "male"

    features = ["pclass", "sex", "age"]

    df = df.dropna(subset=features)

    df_short = df[df.embarked == "Q"]

    y_short = df_short["survived"].values
    A_short = df_short[features].values

    m, n = A_short.shape
    k = int(0.5 * n)

    # Creating variables
    theta = cp.Variable(n)
    weights = cp.Variable(m, nonneg=True)

    # Defining the loss function and the weight constraints
    log_likelihood_samples = cp.multiply(y_short, A_short @ theta) - cp.logistic(A_short @ theta)
    objective = MinimizeMaximize(saddle_inner(weights, log_likelihood_samples))
    constraints = [cp.sum(weights) == k, weights <= 1]

    # Creating and solving the problem
    problem = SaddlePointProblem(objective, constraints)
    problem.solve()
    assert problem.status == cp.OPTIMAL
    robust_theta = theta.value

    # Logistic problem
    log_likelihood = cp.sum(cp.multiply(y_short, A_short @ theta) - cp.logistic(A_short @ theta))

    problem = cp.Problem(cp.Maximize(log_likelihood))
    problem.solve()
    assert problem.status == cp.OPTIMAL

    ols_theta = theta.value

    # Full sample

    y = df["survived"].values
    A = df[features].values

    print(error(A_short @ ols_theta, y_short))
    print(error(A_short @ robust_theta, y_short))
    print(error(A @ ols_theta, y))
    print(error(A @ robust_theta, y))


def error(scores, labels):
    scores[scores > 0] = 1
    scores[scores <= 0] = 0
    return np.sum(np.abs(scores - labels)) / float(np.size(labels))
