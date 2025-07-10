import io
import matplotlib.pyplot as plt

def plot_var_histogram(returns: list[float]) -> io.BytesIO:
    """Histogram of returns with VaR highlighted."""
    buf = io.BytesIO()
    plt.figure()
    plt.hist(returns, bins=30)
    plt.title("Return Distribution")
    plt.xlabel("Log Return")
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf

def plot_stress_scenarios(prices: list[float], shocks: list[float]) -> io.BytesIO:
    """
    Simulate price series paths under ±shock scenarios.
    shocks: e.g. [-0.1, +0.1] for ±10% moves.
    """
    buf = io.BytesIO()
    days = list(range(len(prices)))
    plt.figure()
    plt.plot(days, prices, label="Actual")
    for shock in shocks:
        shocked = [p * (1 + shock) for p in prices]
        plt.plot(days, shocked, "--", label=f"{int(shock*100)}% shock")
    plt.title("Stress Test Scenarios")
    plt.xlabel("Time Step")
    plt.ylabel("Price")
    plt.legend()
    plt.tight_layout()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf
