# gnops-mcp: read-only MCP server, shipped as a container for the
# gemini-rtsw-ci pipeline. The RPM installs a systemd unit; the unit pulls
# and runs the image built from this same commit.

# $GIT_HASH is passed in by build_rpm.sh; git is only the fallback.
%define git_hash %(if [ -n "$GIT_HASH" ]; then echo "$GIT_HASH"; else git rev-parse --short HEAD 2>/dev/null || echo nogit; fi)
%global appimage ghcr.io/gemini-rtsw/gnops-mcp

Name:      gnops-mcp
Version:   0.1.0
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
# Pin the unit to the exact image built alongside this RPM.
sed -e 's|@IMAGE@|%{appimage}:%{version}-git%{git_hash}|' deploy/%{name}.service.in > %{name}.service
if grep -q '@IMAGE@' %{name}.service; then echo "ERROR: placeholder not substituted" >&2; exit 1; fi

%install
install -Dpm 0644 %{name}.service %{buildroot}%{_unitdir}/%{name}.service
# 0600: may hold a Grafana service-account token.
install -Dpm 0600 deploy/%{name}.sysconfig %{buildroot}%{_sysconfdir}/sysconfig/%{name}

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

%changelog
* Fri Oct 16 2026 Hawi Stecher <hawi.stecher@noirlab.edu> - 0.1.0-1
- Initial package: TED telemetry and logs tools from noirlab-ops, plus
  read-only caget/camonitor.
