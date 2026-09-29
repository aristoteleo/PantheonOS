# EVOLVE-BLOCK-START
"""Constructor-based circle packing for n=32 circles"""
import numpy as np


def construct_circles():
    """
    Construct a specific arrangement of 32 circles in a unit square
    that attempts to maximize the sum of their radii.

    Returns:
        np.array of shape (32, 3) with (x, y, r) for each circle
    """
    # Initialize arrays for 32 circles
    n = 32
    centers = np.zeros((n, 2))

    # Place circles in a structured pattern
    # This is a simple pattern - evolution will improve this
    
    # Use a simple grid-based layout as starting point
    grid_size = int(np.ceil(np.sqrt(n)))
    idx = 0
    for i in range(grid_size):
        for j in range(grid_size):
            if idx >= n:
                break
            x = (i + 0.5) / grid_size
            y = (j + 0.5) / grid_size
            centers[idx] = [x, y]
            idx += 1
        if idx >= n:
            break

    # Clip to ensure everything is inside the unit square
    centers = np.clip(centers, 0.01, 0.99)

    # Compute maximum valid radii for this configuration
    radii = compute_max_radii(centers)

    # Combine centers and radii into circles array
    circles = np.column_stack([centers, radii])

    return circles


def compute_max_radii(centers):
    """
    Compute the maximum possible radii for each circle position
    such that they don't overlap and stay within the unit square.

    Args:
        centers: np.array of shape (n, 2) with (x, y) coordinates

    Returns:
        np.array of shape (n) with radius of each circle
    """
    n = centers.shape[0]
    radii = np.ones(n)

    # First, limit by distance to square borders
    for i in range(n):
        x, y = centers[i]
        # Distance to borders
        radii[i] = min(x, y, 1 - x, 1 - y)

    # Then, limit by distance to other circles
    # Each pair of circles with centers at distance d can have
    # sum of radii at most d to avoid overlap
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))

            # If current radii would cause overlap
            if radii[i] + radii[j] > dist:
                # Scale both radii proportionally
                scale = dist / (radii[i] + radii[j]) * 0.99  # 0.99 for safety margin
                radii[i] *= scale
                radii[j] *= scale

    return radii


# EVOLVE-BLOCK-END


# This part remains fixed (not evolved)
def run_code():
    """Run the circle packing constructor for n=32"""
    circles = construct_circles()
    sum_radii = float(np.sum(circles[:, 2]))
    return circles, sum_radii


