package proto

import "time"

// ResourceInventory is a measured snapshot, not a promise of exclusive hardware.
// Fleet reservations protect cooperating managed Apps; external processes can
// still consume memory. Unknown available memory is omitted, never assumed free.
type ResourceInventory struct {
	Protocol     int           `json:"protocol"`
	SampledAt    time.Time     `json:"sampled_at"`
	Memory       MemoryPool    `json:"memory"`
	Accelerators []Accelerator `json:"accelerators"`
}

type MemoryPool struct {
	TotalBytes     uint64  `json:"total_bytes"`
	AvailableBytes *uint64 `json:"available_bytes,omitempty"`
}

type Accelerator struct {
	ID           string     `json:"id"`
	Name         string     `json:"name"`
	Vendor       string     `json:"vendor"`
	Backend      string     `json:"backend"` // cuda | rocm | metal; CPU uses Memory
	Driver       string     `json:"driver,omitempty"`
	Memory       MemoryPool `json:"memory"`
	SharedMemory bool       `json:"shared_memory"` // aliases system RAM, not extra VRAM
}
