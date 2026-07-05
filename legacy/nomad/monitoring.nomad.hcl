job "monitoring" {
  datacenters = ["goida"]
  type        = "service"

  # только swe (89.22.230.5), volumes уже созданы
  constraint {
    attribute = "${attr.unique.network.ip-address}"
    value     = "89.22.230.5"
  }

  group "stack" {
    network {
      mode = "bridge"
      port "grafana" { static = 3000 }
      port "loki"    { static = 3100 }
    }

    # --- loki ----------------------------------------------------------
    task "loki" {
      driver = "docker"

      config {
        image = "grafana/loki:3.0.0"
        ports = ["loki"]
        volumes = [
          "/opt/nomad/volumes/loki:/loki",
          "local/loki.yaml:/etc/loki/local-config.yaml",
        ]
        args = ["-config.file=/etc/loki/local-config.yaml"]
      }

      template {
        destination = "local/loki.yaml"
        data        = <<-EOT
auth_enabled: false
server:
  http_listen_port: 3100
common:
  ring:
    instance_addr: 127.0.0.1
    kvstore:
      store: inmemory
  replication_factor: 1
  path_prefix: /loki
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
storage_config:
  filesystem:
    directory: /loki/chunks
limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 168h
EOT
      }

      resources {
        cpu    = 200
        memory = 256
      }
    }

    # --- grafana -------------------------------------------------------
    task "grafana" {
      driver = "docker"

      config {
        image = "grafana/grafana:10.4.0"
        ports = ["grafana"]
        volumes = [
          "/opt/nomad/volumes/grafana:/var/lib/grafana",
          "local/provisioning:/etc/grafana/provisioning",
        ]
      }

      # grafana_password хранится в: nomad var put nomad/jobs/monitoring grafana_password=<pwd>
      template {
        destination = "local/grafana.env"
        env         = true
        data        = <<-EOT
GF_SERVER_HTTP_PORT=3000
GF_USERS_ALLOW_SIGN_UP=false
GF_AUTH_ANONYMOUS_ENABLED=false
{{ with nomadVar "nomad/jobs/monitoring" -}}
GF_SECURITY_ADMIN_PASSWORD={{ .grafana_password }}
{{- end }}
EOT
      }

      template {
        destination = "local/provisioning/datasources/loki.yaml"
        data        = <<-EOT
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    access: proxy
    url: http://localhost:3100
    isDefault: true
EOT
      }

      resources {
        cpu    = 300
        memory = 512
      }
    }
  }
}
