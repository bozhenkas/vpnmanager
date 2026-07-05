// Package logtail follows an xray access.log and extracts the destination
// hosts that users actually reach, so the geo-analyzer can check which of them
// are really Russian (and thus geoip:ru misses).
package logtail

import (
	"bufio"
	"context"
	"io"
	"os"
	"regexp"
	"strings"
	"time"
)

// Hit is one observed destination extracted from an access-log line.
type Hit struct {
	Host     string // domain or literal IP (no port)
	Port     string
	Proto    string // tcp|udp
	Inbound  string // e.g. GOIDA_SWE
	Outbound string // e.g. REMNA_SWE, DIRECT, BLOCK
}

// xray access-log line, e.g.:
//
//	2026/06/26 22:16:01 from 127.0.0.1:44090 accepted tcp:api2.cursor.sh:443 [GOIDA_SWE -> REMNA_SWE] email: 3
//	... accepted tcp:149.154.167.35:443 [FRA_VLESS_TCP_REALITY_443 >> DIRECT] email: 2
//	... accepted udp:51.77.75.111:5430 [GOIDA_SWE -> REMNA_SWE] email: 3
//
// The host may be a domain, an IPv4, or a bracketed IPv6 ([2a02:...]).
var lineRE = regexp.MustCompile(
	`accepted (tcp|udp):(\[[0-9a-fA-F:]+\]|[^:\s]+):(\d+) \[([^\]]*?)\s*(?:->|>>)\s*([^\]]*?)\]`)

// Follow tails the file at path, parsing each new line and sending Hits to out.
// It handles log rotation (truncation/replacement) by reopening when the inode
// or size shrinks. It returns when ctx is cancelled.
func Follow(ctx context.Context, path string, fromStart bool, out chan<- Hit) error {
	var (
		f      *os.File
		reader *bufio.Reader
		err    error
	)
	open := func() error {
		if f != nil {
			f.Close()
		}
		f, err = os.Open(path)
		if err != nil {
			return err
		}
		if !fromStart {
			f.Seek(0, io.SeekEnd)
		}
		reader = bufio.NewReaderSize(f, 64*1024)
		return nil
	}

	// Wait for the file to appear, then open it.
	for {
		if err = open(); err == nil {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	defer func() {
		if f != nil {
			f.Close()
		}
	}()
	fromStart = true // after the first open, always read any rotated remainder

	lastSize := int64(0)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		line, rerr := reader.ReadString('\n')
		if len(line) > 0 && strings.HasSuffix(line, "\n") {
			if h, ok := parse(line); ok {
				select {
				case out <- h:
				case <-ctx.Done():
					return ctx.Err()
				}
			}
			continue
		}
		// Partial line or EOF: back off, then check for rotation.
		if rerr == io.EOF {
			if st, e := os.Stat(path); e == nil {
				if st.Size() < lastSize { // truncated/rotated → reopen from start
					_ = open()
					lastSize = 0
					continue
				}
				lastSize = st.Size()
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(500 * time.Millisecond):
			}
		}
	}
}

func parse(line string) (Hit, bool) {
	m := lineRE.FindStringSubmatch(line)
	if m == nil {
		return Hit{}, false
	}
	host := strings.Trim(m[2], "[]")
	return Hit{
		Host:     host,
		Port:     m[3],
		Proto:    m[1],
		Inbound:  strings.TrimSpace(m[4]),
		Outbound: strings.TrimSpace(m[5]),
	}, true
}
