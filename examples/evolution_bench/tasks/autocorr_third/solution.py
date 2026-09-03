# EVOLVE-BLOCK-START
"""Constructor-based function for C₃ autoconvolution optimization"""
import numpy as np


def construct_function():
    """
    Construct a discrete function on [-1/4, 1/4] that attempts to minimize
    C₃ = 2·n·max(|conv(f,f)|) / (∑f)²

    Returns:
        np.array of shape (n,) with function values (heights)
    """
    # Choose length - this is a design variable that evolution can explore
    n = 400  # Starting point - evolution will explore other lengths

    # Initialize function values
    x = np.linspace(-0.25, 0.25, n)

    # Strategy: Start with a smooth bell-shaped profile
    # Create a Gaussian-like envelope
    center = 0.0
    width = 0.1
    heights = np.exp(-((x - center) ** 2) / (2 * width ** 2))

    # Add some oscillatory components to explore phase cancellation
    freq1 = 8.0
    freq2 = 16.0
    heights += 0.3 * np.cos(2 * np.pi * freq1 * x)
    heights += 0.15 * np.sin(2 * np.pi * freq2 * x)

    # Normalize to have reasonable sum
    target_sum = 25.0
    heights = heights * (target_sum / np.sum(heights))

    return heights


def compute_c3(heights):
    """
    Compute the C₃ autoconvolution constant for given function values.

    C₃ = 2·n·max(|conv(f,f)|) / (∑f)²

    Args:
        heights: np.array of shape (n,) with function values

    Returns:
        float: The C₃ value
    """
    n = len(heights)
    dx = 0.5 / n
    integral_f_sq = (np.sum(heights) * dx) ** 2
    if integral_f_sq < 1e-9:
        raise ValueError("Function integral is close to zero, ratio is unstable.")

    conv_full = np.convolve(heights, heights, mode="full")
    scaled_conv = conv_full * dx
    c3 = np.max(np.abs(scaled_conv)) / integral_f_sq
    return float(c3)


# EVOLVE-BLOCK-END


# This part remains fixed (not evolved)
def run_code():
    """Run the C₃ optimization constructor"""
    heights = construct_function()
    c3_value = compute_c3(heights)
    return heights, c3_value, c3_value, len(heights)
