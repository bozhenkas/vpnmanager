package detect

import "testing"

func TestConsensus(t *testing.T) {
	cases := []struct {
		name   string
		s      Signals
		wantRU bool
	}{
		{"both authoritative RU", Signals{CymruCC: "RU", RDAPCountry: "RU", RTTms: -1}, true},
		{"one authoritative RU + ptr", Signals{CymruCC: "RU", PTR: "host.example.ru", RTTms: -1}, true},
		{"authoritative conflict → no", Signals{CymruCC: "RU", RDAPCountry: "DE", RTTms: -1}, false},
		{"foreign", Signals{CymruCC: "US", RDAPCountry: "US", RTTms: -1}, false},
		{"only ptr hint, no authority → no", Signals{PTR: "x.ru", RTTms: -1}, false},
		{"RU + fast RTT", Signals{CymruCC: "RU", RTTms: 8, RTTMaxMs: 18}, true},
		{"no signals", Signals{RTTms: -1}, false},
		{"RDAP RU only", Signals{RDAPCountry: "RU", RTTms: -1}, true},
	}
	for _, c := range cases {
		gotRU, conf, _ := Consensus(c.s)
		if gotRU != c.wantRU {
			t.Errorf("%s: Consensus=%v (conf %.2f); want %v", c.name, gotRU, conf, c.wantRU)
		}
		if gotRU && conf < 0.5 {
			t.Errorf("%s: RU verdict but low confidence %.2f", c.name, conf)
		}
	}
}

func TestConsensusSignalCount(t *testing.T) {
	// Cymru + RTT both RU → 2 independent signals (auto-promotable).
	_, _, n := Consensus(Signals{CymruCC: "RU", RTTms: 8, RTTMaxMs: 18})
	if n != 2 {
		t.Errorf("Cymru+RTT: ruSignals=%d want 2", n)
	}
	// Cymru only → 1 signal (review-only).
	_, _, n = Consensus(Signals{CymruCC: "RU", RTTms: -1})
	if n != 1 {
		t.Errorf("Cymru only: ruSignals=%d want 1", n)
	}
	// PTR is a hint, not a strong signal — does not count.
	_, _, n = Consensus(Signals{CymruCC: "RU", PTR: "x.ru", RTTms: -1})
	if n != 1 {
		t.Errorf("Cymru+PTR: ruSignals=%d want 1 (PTR not strong)", n)
	}
}

func TestHasRUSuffix(t *testing.T) {
	yes := []string{"a.ru", "x.y.su", "host.example.ru."}
	no := []string{"a.com", "ru.example.com", "a.run"}
	for _, p := range yes {
		if !hasRUSuffix(p) {
			t.Errorf("hasRUSuffix(%q)=false want true", p)
		}
	}
	for _, p := range no {
		if hasRUSuffix(p) {
			t.Errorf("hasRUSuffix(%q)=true want false", p)
		}
	}
}
