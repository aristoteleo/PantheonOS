# Environment capture
Timestamp: 2025-11-27 01:10:00 UTC

## System
- OS: Linux tag-308 5.15.0-161-generic #171-Ubuntu SMP Sat Oct 11 08:17:01 UTC 2025 x86_64 x86_64 x86_64 GNU/Linux
- CPU:
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           46 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  56
On-line CPU(s) list:                     0-55
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Xeon(R) Platinum 8280 CPU @ 2.60GHz
CPU family:                              6
Model:                                   85
Thread(s) per core:                      1
Core(s) per socket:                      28
- Memory: MemTotal:       1584966256 kB

## Python
- Interpreter: 3.10.19 (main, Oct 21 2025, 16:43:05) [GCC 11.2.0]
- Environment: gps

## GPU
torch not available

## Key packages
scanpy==1.11.5
anndata==0.11.4
numpy==1.26.4
scipy==1.15.3
pandas==1.5.3
scikit-learn==1.7.2
matplotlib==3.10.7
seaborn==0.13.2
statsmodels==0.14.5
scgeneFit==1.0.0
spapros==0.1.5
umap-learn==0.5.9.post2
pynndescent==0.5.13
louvain==0.8.2
leidenalg==0.11.0
skmisc==0.0.0


## Validation
- skmisc successfully installed in env 'gps'.
- scanpy pp.highly_variable_genes with flavor='seurat_v3' executed on test AnnData and returned n_top=200 HVGs.
