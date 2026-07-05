package logtail

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFollowReadsExistingAndAppended(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "access.log")
	if err := os.WriteFile(path,
		[]byte("2026/06/26 00:00:00 from 127.0.0.1:1 accepted tcp:a.ru:443 [IN -> OUT] email: 1\n"),
		0o644); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out := make(chan Hit, 8)
	go func() { _ = Follow(ctx, path, true, out) }()

	got := map[string]bool{}
	// append a second line shortly after start to exercise the tail path
	go func() {
		time.Sleep(200 * time.Millisecond)
		f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
		f.WriteString("2026/06/26 00:00:01 from 127.0.0.1:2 accepted tcp:b.com:80 [IN -> OUT] email: 1\n")
		f.Close()
	}()

	deadline := time.After(2 * time.Second)
	for len(got) < 2 {
		select {
		case h := <-out:
			got[h.Host] = true
		case <-deadline:
			t.Fatalf("timed out; got %v", got)
		}
	}
	if !got["a.ru"] || !got["b.com"] {
		t.Fatalf("missing hits, got %v", got)
	}
}
