package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
)

// Local-only provisioning. No key flag, remote API, key listing or key logging.
func modelCredentials(args []string, input io.Reader, output io.Writer) error {
	if len(args) == 0 {
		return errors.New("use credentials put, list or delete")
	}
	action := args[0]
	fs := flag.NewFlagSet("credentials", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	state := fs.String("state-dir", defaultStateDir(), "node state directory")
	fleet := fs.String("fleet", "", "Fleet ID")
	name := fs.String("name", "", "credential name")
	endpoint := fs.String("endpoint", "", "authorized API base URL")
	file := fs.String("file", "", "local file containing the API key")
	stdin := fs.Bool("stdin", false, "read key from stdin")
	replace := fs.Bool("replace", false, "explicitly replace an existing credential")
	if fs.Parse(args[1:]) != nil || fs.NArg() != 0 {
		return errors.New("invalid credentials options; API keys must be supplied through --file or --stdin")
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`).MatchString(*fleet) {
		return errors.New("supply the exact Fleet ID with --fleet")
	}
	root := filepath.Join(*state, "apps", *fleet, "model-credentials")
	ref := modelcredentials.Prefix + *name
	switch action {
	case "put":
		if (*file == "") == !*stdin {
			return errors.New("choose exactly one of --file or --stdin")
		}
		source := input
		if *file != "" {
			f, err := os.Open(*file)
			if err != nil {
				return errors.New("cannot read credential input file")
			}
			defer f.Close()
			source = f
		}
		data, err := io.ReadAll(io.LimitReader(source, 8195))
		defer clear(data)
		if err != nil || len(data) > 8194 {
			return modelcredentials.ErrCredential
		}
		if err = modelcredentials.Put(root, ref, *endpoint, strings.TrimSpace(string(data)), *replace); err != nil {
			return err
		}
		_, err = fmt.Fprintln(output, "Stored "+ref)
		return err
	case "list":
		refs, err := modelcredentials.List(root)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(refs)
	case "delete":
		if err := modelcredentials.Delete(root, ref); err != nil {
			return err
		}
		_, err := fmt.Fprintln(output, "Removed "+ref)
		return err
	default:
		return errors.New("use credentials put, list or delete")
	}
}

// Private child-process protocol. The connector receives a key through its pipe;
// the caller supplies only a reference/endpoint, never paths or Fleet auth keys.
func readModelCredential(input io.Reader, output io.Writer) error {
	root := os.Getenv("PANTHEON_MODEL_CREDENTIALS")
	if !filepath.IsAbs(root) {
		return modelcredentials.ErrCredential
	}
	raw, err := io.ReadAll(io.LimitReader(input, 4097))
	var req struct {
		Ref      string `json:"ref"`
		Endpoint string `json:"endpoint"`
	}
	if err != nil || len(raw) > 4096 || json.Unmarshal(raw, &req) != nil {
		return modelcredentials.ErrCredential
	}
	key, err := modelcredentials.Read(root, req.Ref, req.Endpoint)
	if err != nil {
		return modelcredentials.ErrCredential
	}
	return json.NewEncoder(output).Encode(map[string]string{"key": key})
}
