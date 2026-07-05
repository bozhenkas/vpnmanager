// Package store tracks observed hosts, dedupes them, remembers verdicts across
// restarts, and emits candidates.json — the review file the python promoter
// reads. It never mutates the routing lists itself.
package store

import (
	"encoding/json"
	"os"
	"sort"
	"sync"
	"time"

	"ru-geo-analyzer/internal/detect"
)

type Host struct {
	Host      string          `json:"host"`
	Type      string          `json:"type"`
	Hits      int             `json:"hits"`
	FirstSeen time.Time       `json:"first_seen"`
	LastSeen  time.Time       `json:"last_seen"`
	CheckedAt time.Time       `json:"checked_at,omitempty"`
	Verdict   *detect.Verdict `json:"verdict,omitempty"`
	Confirmed bool            `json:"confirmed"` // qualifies for fully-automatic promotion
	LastPort  string          `json:"-"`
	pending   bool            // a check is already queued/in-flight (transient)
}

// AutoPolicy is the gate for fully-automatic (no human) promotion.
type AutoPolicy struct {
	MinSignals int           // independent RU signals required (>=2)
	MinHits    int           // distinct sightings required
	MinAge     time.Duration // host must have been observed for at least this long
}

type Store struct {
	mu        sync.Mutex
	hosts     map[string]*Host
	statePath string
	maxHosts  int
	recheck   time.Duration
}

func New(statePath string, maxHosts int, recheck time.Duration) *Store {
	s := &Store{
		hosts:     make(map[string]*Host),
		statePath: statePath,
		maxHosts:  maxHosts,
		recheck:   recheck,
	}
	s.load()
	return s
}

// Observe records a sighting and reports whether the host needs a (re)check:
// true when it is new or its verdict is older than the recheck interval.
func (s *Store) Observe(host, typ, port string) bool {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	h, ok := s.hosts[host]
	if !ok {
		if len(s.hosts) >= s.maxHosts {
			s.evictLocked()
		}
		h = &Host{Host: host, Type: typ, FirstSeen: now}
		s.hosts[host] = h
	}
	h.Hits++
	h.LastSeen = now
	h.LastPort = port
	if h.pending {
		return false // a check is already queued/running — don't enqueue twice
	}
	stale := h.Verdict == nil || now.Sub(h.CheckedAt) > s.recheck
	if stale {
		h.pending = true
	}
	return stale
}

func (s *Store) SetVerdict(host string, v detect.Verdict) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if h, ok := s.hosts[host]; ok {
		v2 := v
		h.Verdict = &v2
		h.CheckedAt = time.Now()
		h.pending = false
	}
}

func (s *Store) Port(host string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if h, ok := s.hosts[host]; ok {
		return h.LastPort
	}
	return ""
}

// evictLocked drops the least-recently-seen host (caller holds the lock).
func (s *Store) evictLocked() {
	var oldestKey string
	var oldest time.Time
	first := true
	for k, h := range s.hosts {
		if first || h.LastSeen.Before(oldest) {
			oldest, oldestKey, first = h.LastSeen, k, false
		}
	}
	if oldestKey != "" {
		delete(s.hosts, oldestKey)
	}
}

type candidatesFile struct {
	GeneratedAt time.Time `json:"generated_at"`
	MinConf     float64   `json:"min_confidence"`
	MinHits     int       `json:"min_hits"`
	Count       int       `json:"count"`
	Candidates  []*Host   `json:"candidates"`
}

// WriteCandidates atomically writes RU hosts above the review thresholds, each
// tagged with Confirmed (true → the updater may auto-promote without review).
func (s *Store) WriteCandidates(path string, minConf float64, minHits int, auto AutoPolicy, now time.Time) error {
	s.mu.Lock()
	out := make([]*Host, 0)
	for _, h := range s.hosts {
		if h.Verdict != nil && h.Verdict.IsRU && h.Verdict.Confidence >= minConf && h.Hits >= minHits {
			h.Confirmed = h.Verdict.RUSignals >= auto.MinSignals &&
				h.Hits >= auto.MinHits &&
				now.Sub(h.FirstSeen) >= auto.MinAge
			out = append(out, h)
		}
	}
	s.mu.Unlock()

	sort.Slice(out, func(i, j int) bool {
		if out[i].Verdict.Confidence != out[j].Verdict.Confidence {
			return out[i].Verdict.Confidence > out[j].Verdict.Confidence
		}
		return out[i].Hits > out[j].Hits
	})

	cf := candidatesFile{
		GeneratedAt: now,
		MinConf:     minConf,
		MinHits:     minHits,
		Count:       len(out),
		Candidates:  out,
	}
	return writeJSONAtomic(path, cf)
}

// --- persistence ---

func (s *Store) Save() error {
	s.mu.Lock()
	list := make([]*Host, 0, len(s.hosts))
	for _, h := range s.hosts {
		list = append(list, h)
	}
	s.mu.Unlock()
	return writeJSONAtomic(s.statePath, list)
}

func (s *Store) load() {
	b, err := os.ReadFile(s.statePath)
	if err != nil {
		return
	}
	var list []*Host
	if json.Unmarshal(b, &list) != nil {
		return
	}
	for _, h := range list {
		if h != nil && h.Host != "" {
			s.hosts[h.Host] = h
		}
	}
}

func (s *Store) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.hosts)
}

func writeJSONAtomic(path string, v any) error {
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
