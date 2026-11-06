# gnops-mcp: read-only MCP server, shipped as a container for the
# gemini-rtsw-ci pipeline. The RPM installs a systemd unit; the unit pulls
# and runs the image built from this same commit. Hosts without sudo run the
# same image with docker compose instead; the RPM carries those files too
# (%{_datadir}/%{name}: gnops-mcp.yaml and gnops-mcp.env).

# $GIT_HASH is passed in by build_rpm.sh; git is only the fallback.
%define git_hash %(if [ -n "$GIT_HASH" ]; then echo "$GIT_HASH"; else git rev-parse --short HEAD 2>/dev/null || echo nogit; fi)
%global appimage ghcr.io/gemini-rtsw/gnops-mcp

Name:      gnops-mcp
Version:   0.1.3
Release:   1.git%{git_hash}%{?dist}
Summary:   Read-only MCP server for TED telemetry, logs and live EPICS (caget/camonitor)
License:   Proprietary
Source0:   %{name}-%{version}.tar.gz
BuildArch: noarch
BuildRequires: systemd-rpm-macros
Requires:  systemd

%description
Runs the gnops-mcp container as a systemd service. It serves an MCP endpoint
(http://<host>:8001/mcp) with read-only tools: TED telemetry from the
Archiver Appliance and TED logs from Elasticsearch (both through Grafana), and
live EPICS values through Channel Access caget/camonitor. There is no put.

%prep
%autosetup

%build
# Pin the unit and the container settings to the exact image built alongside this RPM.
sed -e 's|@IMAGE@|%{appimage}:%{version}-git%{git_hash}|' deploy/%{name}.service.in > %{name}.service
sed -e 's|@IMAGE@|%{appimage}:%{version}-git%{git_hash}|' deploy/%{name}.sysconfig > %{name}.env
if grep -q '@IMAGE@' %{name}.service %{name}.env; then echo "ERROR: placeholder not substituted" >&2; exit 1; fi

%install
install -Dpm 0644 %{name}.service %{buildroot}%{_unitdir}/%{name}.service
# 0600: may hold a Grafana service-account token.
install -Dpm 0600 %{name}.env %{buildroot}%{_sysconfdir}/sysconfig/%{name}
# For the container rollout (README, Install): copied to the user's home.
install -Dpm 0644 deploy/%{name}.yaml %{buildroot}%{_datadir}/%{name}/%{name}.yaml
install -Dpm 0644 %{name}.env %{buildroot}%{_datadir}/%{name}/%{name}.env

%post
%systemd_post %{name}.service
%preun
%systemd_preun %{name}.service
%postun
%systemd_postun %{name}.service

%files
# Not a config file: the unit carries the image tag, so upgrades must overwrite it.
%{_unitdir}/%{name}.service
%config(noreplace) %{_sysconfdir}/sysconfig/%{name}
%{_datadir}/%{name}/

%changelog
* Fri Nov 06 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.3-1
- Allow the host's short name, not only its FQDN.
- Default to the MK Channel Access address list used by TED's archiver.
- Ship gnops-mcp.yaml and gnops-mcp.env for running the image with docker
  compose on hosts without sudo; the settings file names its pinned IMAGE.

* Thu Nov 05 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.2-1
- Limit caget and camonitor to CA_MAX_PVS_PER_MIN PVs per minute (default
  200) across all clients; a refused call says why and when to retry.

* Fri Oct 30 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.1-1
- Cap concurrent Grafana requests and CA calls (MAX_CONCURRENT, default 5).

* Fri Oct 16 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.0-1
- Initial package: TED telemetry and logs tools from noirlab-ops, plus
  read-only caget/camonitor.
