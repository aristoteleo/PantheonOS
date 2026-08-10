# EVOLVE-BLOCK-START
"""Constructor-based Hadamard matrix optimization for n=29"""
import numpy as np
import random


def construct_hadamard_matrix(n=29):
    """
    Construct a Hadamard-like matrix of size n using optimization methods.
    
    Args:
        n: Matrix size (default 29)
        
    Returns:
        n x n matrix with entries +1 or -1
    """
    
    def det_bareiss(A):
        """Bareiss algorithm for exact integer determinant calculation."""
        size = len(A)
        if size == 0:
            return 1
        M = [row.copy() for row in A]
        for k in range(size - 1):
            if M[k][k] == 0:
                for i in range(k + 1, size):
                    if M[i][k] != 0:
                        M[k], M[i] = M[i], M[k]
                        break
                else:
                    return 0
            for i in range(k + 1, size):
                for j in range(k + 1, size):
                    num = M[i][j] * M[k][k] - M[i][k] * M[k][j]
                    den = M[k - 1][k - 1] if k > 0 else 1
                    M[i][j] = num // den
        return M[-1][-1]

    def hill_climb_with_annealing(A, max_iters=2000, seed=None, temp0=0.5):
        """Hill climbing with simulated annealing for Hadamard matrix optimization."""
        rng = random.Random(seed)
        size = len(A)
        current_matrix = [row.copy() for row in A]
        det_curr = det_bareiss(current_matrix)
        best_det = det_curr
        best_matrix = [row.copy() for row in current_matrix]

        for t in range(1, max_iters + 1):
            # Random flip
            i = rng.randrange(size)
            j = rng.randrange(size)
            old_val = current_matrix[i][j]
            current_matrix[i][j] = -old_val
            
            # Calculate new determinant
            d_new = det_bareiss(current_matrix)
            
            # Accept or reject based on simulated annealing
            accept = False
            if abs(d_new) >= abs(det_curr):
                accept = True
            else:
                # Annealing temperature schedule
                T = temp0 / (1.0 + t * 0.001)
                if T > 0 and rng.random() < np.exp((abs(d_new) - abs(det_curr)) / max(1.0, T * abs(det_curr))):
                    accept = True
            
            if accept:
                det_curr = d_new
                if abs(det_curr) > abs(best_det):
                    best_det = det_curr
                    best_matrix = [row.copy() for row in current_matrix]
            else:
                current_matrix[i][j] = old_val  # Revert

        return np.array(best_matrix), best_det

    def create_structured_start(size):
        """Create a structured starting matrix for optimization."""
        # For N=29, use quadratic residues mod 29
        if size == 29:
            matrix = []
            quadratic_residues = {1, 4, 5, 6, 7, 9, 13, 16, 20, 22, 23, 24, 25, 28}  # QR mod 29
            for i in range(size):
                row = []
                for j in range(size):
                    diff = (i - j) % size
                    if diff in quadratic_residues:
                        row.append(1)
                    else:
                        row.append(-1)
                matrix.append(row)
            return matrix
        
        # For other sizes with n % 4 == 1, try quadratic residue approach
        elif size > 2 and size % 4 == 1:
            try:
                quadratic_residues = set()
                for i in range(1, size):
                    quadratic_residues.add((i * i) % size)
                
                matrix = []
                for i in range(size):
                    row = []
                    for j in range(size):
                        diff = (i - j) % size
                        if diff in quadratic_residues:
                            row.append(1)
                        else:
                            row.append(-1)
                    matrix.append(row)
                return matrix
            except Exception:
                pass
        
        # Random starting point for other cases
        return np.random.choice([-1, 1], size=(size, size)).tolist()

    # Create starting matrix
    start_matrix = create_structured_start(n)
    
    # Optimize using hill climbing with annealing
    optimized_matrix, _ = hill_climb_with_annealing(
        start_matrix, 
        max_iters=2000,
        seed=42,
        temp0=0.5
    )
    
    return optimized_matrix


# EVOLVE-BLOCK-END


# Fixed API for evaluator
def run_code():
    """
    Run the Hadamard matrix constructor for n=29.
    
    Returns:
        Tuple of (matrix,) where matrix is an (29, 29) array with entries ±1
    """
    matrix = construct_hadamard_matrix(n=29)
    return (matrix,)


if __name__ == "__main__":
    matrix = run_code()[0]
    print(f"Constructed Hadamard matrix of size {matrix.shape[0]}x{matrix.shape[1]}")
    # Calculate determinant for verification
    det_val = np.linalg.det(matrix.astype(float))
    print(f"Determinant: {abs(det_val):.2e}")
