package store

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"ru-geo-analyzer/internal/detect"
)

func TestObserveDedupAndRecheck(t *testing.T) {
	s := New(filepath.Join(t.TempDir(), "state.json"), 100, time.Hour)

	if !s.Observe("a.com", "domain", "443") {
		t.Fatal("first Observe should need a check")
	}
	// While pending (no verdict yet) further sightings must NOT re-enqueue.
	if s.Observe("a.com", "domain", "443") {
		t.Fatal("second Observe while pending should not need a check")
	}
	s.SetVerdict("a.com", detect.Verdict{Host: "a.com", IsRU: true, Confidence: 0.9})
	// Fresh verdict, within recheck window → no check.
	if s.Observe("a.com", "domain", "443") {
		t.Fatal("Observe with fresh verdict should not need a check")
	}
}

func TestObserveRecheckStale(t *testing.T) {
	s := New(filepath.Join(t.TempDir(), "state.json"), 100, time.Nanosecond)
	s.Observe("b.com", "domain", "443")
	s.SetVerdict("b.com", detect.Verdict{Host: "b.com", IsRU: false, Confidence: 0.2})
	time.Sleep(2 * time.Millisecond)
	if !s.Observe("b.com", "domain", "443") {
		t.Fatal("stale verdict should need a recheck")
	}
}

func TestWriteCandidatesFilters(t *testing.T) {
	dir := t.TempDir()
	s := New(filepath.Join(dir, "state.json"), 100, time.Hour)

	add := func(host string, isRU bool, conf float64, hits int) {
		s.Observe(host, "domain", "443")
		for i := 1; i < hits; i++ {
			s.Observe(host, "domain", "443")
		}
		s.SetVerdict(host, detect.Verdict{Host: host, IsRU: isRU, Confidence: conf})
	}
	add("ru-high.example.com", true, 0.95, 10) // passes
	add("ru-lowconf.example.com", true, 0.50, 10) // fails conf
	add("ru-lowhits.example.com", true, 0.95, 1)  // fails hits
	add("foreign.example.com", false, 0.99, 10)   // not RU

	candPath := filepath.Join(dir, "candidates.json")
	auto := AutoPolicy{MinSignals: 2, MinHits: 3, MinAge: 0}
	if err := s.WriteCandidates(candPath, 0.75, 3, auto, time.Now()); err != nil {
		t.Fatal(err)
	}
	var cf candidatesFile
	b, _ := os.ReadFile(candPath)
	if err := json.Unmarshal(b, &cf); err != nil {
		t.Fatal(err)
	}
	if cf.Count != 1 || len(cf.Candidates) != 1 || cf.Candidates[0].Host != "ru-high.example.com" {
		t.Fatalf("expected only ru-high.example.com, got count=%d %+v", cf.Count, cf.Candidates)
	}
}

func TestConfirmedGate(t *testing.T) {
	dir := t.TempDir()
	s := New(filepath.Join(dir, "state.json"), 100, time.Hour)
	mk := func(host string, signals, hits int) {
		s.Observe(host, "domain", "443")
		for i := 1; i < hits; i++ {
			s.Observe(host, "domain", "443")
		}
		s.SetVerdict(host, detect.Verdict{Host: host, IsRU: true, Confidence: 1.0, RUSignals: signals})
	}
	mk("two-sig.example.com", 2, 5)  // confirmed
	mk("one-sig.example.com", 1, 5)  // signals too few → review only
	mk("few-hits.example.com", 2, 2) // hits too few → review only

	candPath := filepath.Join(dir, "candidates.json")
	auto := AutoPolicy{MinSignals: 2, MinHits: 5, MinAge: 0}
	if err := s.WriteCandidates(candPath, 0.75, 2, auto, time.Now()); err != nil {
		t.Fatal(err)
	}
	var cf candidatesFile
	b, _ := os.ReadFile(candPath)
	json.Unmarshal(b, &cf)
	confirmed := map[string]bool{}
	for _, h := range cf.Candidates {
		confirmed[h.Host] = h.Confirmed
	}
	if !confirmed["two-sig.example.com"] {
		t.Error("two-sig should be confirmed")
	}
	if confirmed["one-sig.example.com"] {
		t.Error("one-sig must NOT be confirmed (1 signal)")
	}
	if confirmed["few-hits.example.com"] {
		t.Error("few-hits must NOT be confirmed (hits<min)")
	}
}

func TestSaveLoadRoundtrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	s := New(path, 100, time.Hour)
	s.Observe("keep.example.com", "domain", "443")
	s.SetVerdict("keep.example.com", detect.Verdict{Host: "keep.example.com", IsRU: true, Confidence: 0.9})
	if err := s.Save(); err != nil {
		t.Fatal(err)
	}
	s2 := New(path, 100, time.Hour)
	if s2.Len() != 1 {
		t.Fatalf("reloaded store has %d hosts, want 1", s2.Len())
	}
	// reloaded verdict is fresh → no recheck
	if s2.Observe("keep.example.com", "domain", "443") {
		t.Fatal("reloaded fresh verdict should not need a check")
	}
}

func TestEviction(t *testing.T) {
	s := New(filepath.Join(t.TempDir(), "state.json"), 2, time.Hour)
	s.Observe("a", "domain", "1")
	time.Sleep(time.Millisecond)
	s.Observe("b", "domain", "1")
	time.Sleep(time.Millisecond)
	s.Observe("c", "domain", "1") // triggers eviction of oldest ("a")
	if s.Len() > 2 {
		t.Fatalf("store exceeded max: %d", s.Len())
	}
}
