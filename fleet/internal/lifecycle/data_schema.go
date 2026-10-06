package lifecycle

import "fmt"

// DataSchema is an App's durable format contract, not its release version.
// Accepted older formats require App-owned admission/migration before serving.
// Missing declarations preserve legacy-to-legacy copies only.
type DataSchema struct {
	ID      string `json:"id"`
	Version int    `json:"version"`
	Accepts []int  `json:"accepts"`
}

func (s *DataSchema) Validate() error {
	if s == nil {
		return nil
	}
	if !nameRE.MatchString(s.ID) || s.Version < 1 || s.Version > 2147483647 || len(s.Accepts) < 1 || len(s.Accepts) > 64 {
		return fmt.Errorf("invalid App data schema declaration")
	}
	seen := map[int]bool{}
	for _, v := range s.Accepts {
		if v < 1 || v > 2147483647 || seen[v] {
			return fmt.Errorf("invalid accepted App data versions")
		}
		seen[v] = true
	}
	if !seen[s.Version] {
		return fmt.Errorf("App data schema must accept its written version")
	}
	return nil
}

func compatibleDataSchemas(source, target *DataSchema) error {
	if err := source.Validate(); err != nil {
		return err
	}
	if err := target.Validate(); err != nil {
		return err
	}
	if source == nil && target == nil {
		return nil
	}
	if source != nil && target != nil && source.ID == target.ID {
		for _, version := range target.Accepts {
			if version == source.Version {
				return nil
			}
		}
	}
	return fmt.Errorf("target App cannot open source data schema; explicit migration is required")
}
