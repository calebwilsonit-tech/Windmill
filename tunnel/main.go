// windmill-tunnel: Tailscale built into Windmill - so your phone can reach Windmill on this computer from anywhere,
// with nothing else to install.
//
// It joins your Tailscale network as its own small device (no Tailscale app needed) and publishes Windmill's
// phone entry point at a permanent HTTPS address with Tailscale Funnel, e.g. https://windmill-pc.tail1234.ts.net.
// Every request gets the header "X-Windmill-Via: tunnel" so Windmill knows it came from the internet - Windmill
// then only accepts phones that were already paired at home (pairing new phones is home-Wi-Fi only).
//
// It reports what's happening as one JSON object per line on stdout, which Windmill shows in its Phone page:
//
//	{"state":"starting"}
//	{"state":"login","url":"https://login.tailscale.com/a/..."}   - sign in once in the browser
//	{"state":"ready","url":"https://windmill-pc.tail1234.ts.net"}
//	{"state":"error","message":"...","url":"https://..."}         - e.g. a link to enable Funnel
package main

import (
	"context"
	"encoding/json"
	"flag"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"tailscale.com/tsnet"
)

func say(v map[string]string) {
	b, _ := json.Marshal(v)
	os.Stdout.Write(append(b, '\n'))
}

var linkRe = regexp.MustCompile(`https://\S+`)

func main() {
	dir := flag.String("state", "", "folder for this device's Tailscale identity")
	hostname := flag.String("hostname", "windmill", "device name in your Tailscale network")
	target := flag.String("target", "127.0.0.1:8765", "Windmill's phone entry point")
	flag.Parse()

	say(map[string]string{"state": "starting"})
	srv := &tsnet.Server{
		Dir:      *dir,
		Hostname: *hostname,
		Logf:     func(string, ...any) {}, // quiet: Windmill shows the JSON lines only
		UserLogf: func(format string, args ...any) {},
	}
	if err := srv.Start(); err != nil {
		say(map[string]string{"state": "error", "message": err.Error()})
		os.Exit(1)
	}
	defer srv.Close()

	lc, err := srv.LocalClient()
	if err != nil {
		say(map[string]string{"state": "error", "message": err.Error()})
		os.Exit(1)
	}
	// Wait until this device is signed in (show the sign-in link while it isn't).
	ctx := context.Background()
	lastLogin := ""
	for {
		st, err := lc.Status(ctx)
		if err == nil {
			if st.BackendState == "Running" && st.Self != nil {
				break
			}
			if st.AuthURL != "" && st.AuthURL != lastLogin {
				lastLogin = st.AuthURL
				say(map[string]string{"state": "login", "url": st.AuthURL})
			}
		}
		time.Sleep(time.Second)
	}

	// Public HTTPS (certificate handled by Tailscale). A new Tailscale network needs two switches on Tailscale's
	// site first (HTTPS certificates, then Funnel) - say which, link straight to it, and keep trying, so it comes up
	// by itself as soon as the user flips them.
	var ln net.Listener
	lastMsg := ""
	for {
		ln, err = srv.ListenFunnel("tcp", ":443")
		if err == nil {
			break
		}
		msg := err.Error()
		if msg != lastMsg {
			lastMsg = msg
			st, _ := lc.Status(ctx)
			fix, step := linkRe.FindString(msg), ""
			low := strings.ToLower(msg)
			switch {
			case st != nil && st.Self != nil && (strings.Contains(low, "https") || strings.Contains(low, "funnel")):
				// Tailscale's own one-click approval page for this device: turns on HTTPS certificates AND Funnel
				fix, step = "https://login.tailscale.com/f/funnel?node="+string(st.Self.ID), "approve"
			case strings.Contains(low, "https"):
				fix, step = "https://login.tailscale.com/admin/dns", "https"
			}
			say(map[string]string{"state": "setup", "step": step, "message": msg, "url": fix})
		}
		time.Sleep(5 * time.Second)
	}
	st, _ := lc.Status(ctx)
	name := strings.TrimSuffix(st.Self.DNSName, ".")
	say(map[string]string{"state": "ready", "url": "https://" + name})

	to, _ := url.Parse("http://" + *target)
	proxy := httputil.NewSingleHostReverseProxy(to)
	base := proxy.Director
	proxy.Director = func(r *http.Request) {
		base(r)
		r.Header.Set("X-Windmill-Via", "tunnel") // can't be faked from outside: always overwritten here
		r.Host = r.Header.Get("X-Forwarded-Host")
		if r.Host == "" {
			r.Host = name
		}
	}
	proxy.FlushInterval = -1 // stream live chat events (server-sent events) straight through
	if err := http.Serve(ln, proxy); err != nil {
		say(map[string]string{"state": "error", "message": err.Error()})
		os.Exit(3)
	}
}
