# -*- mode: ruby -*-
# vi: set ft=ruby :
# =============================================================================
#  Vagrantfile - Infrastructure InsaCloud : 1 contrôleur + 2 workers
# -----------------------------------------------------------------------------
#    controller  192.168.56.10  site web (nginx + Flask/Gunicorn), Faucheur, watchdog
#    worker1     192.168.56.11  Docker : héberge les machines louées
#    worker2     192.168.56.12  Docker : héberge les machines louées
#
#  Le contrôleur pilote les workers via `docker -H ssh://` (clé SSH dédiée,
#  aucune API Docker exposée). Les conteneurs sont répartis sur le worker le
#  moins chargé et survivent aux redémarrages (--restart=always, live-restore).
#
#  Fournisseurs (Vagrant prend le premier utilisable) :
#    vmware_desktop (Mac Apple Silicon / Intel)   vagrant plugin install vagrant-vmware-desktop
#    parallels      (Mac)                          vagrant plugin install vagrant-parallels
#    qemu           (Mac Apple Silicon, libre)     brew install qemu && vagrant plugin install vagrant-qemu
#    virtualbox     (PC Windows / Linux, Mac Intel)
#
#  Commandes :
#    vagrant up                     # les 3 VM
#    vagrant up controller worker1  # un sous-ensemble
#    vagrant status / ssh worker1 / halt / destroy -f
#  Puis : cd 3_ansible && ansible-playbook site.yml --ask-vault-pass
# =============================================================================

BOX = "bento/ubuntu-22.04"   # existe en amd64 et arm64 (VMware, Parallels, VirtualBox)

MACHINES = {
  "controller" => { "ip" => "192.168.56.10", "memory" => 2048, "cpus" => 2 },
  "worker1"    => { "ip" => "192.168.56.11", "memory" => 3072, "cpus" => 2 },
  "worker2"    => { "ip" => "192.168.56.12", "memory" => 3072, "cpus" => 2 },
}

# Architecture du poste (le Ruby embarqué par Vagrant peut tourner sous Rosetta)
HOST_ARM64 = (RUBY_PLATFORM =~ /arm64|aarch64/) ||
             (`uname -m 2>/dev/null`.strip =~ /arm64|aarch64/ rescue false) ? true : false

Vagrant.configure("2") do |config|
  config.vm.box = BOX
  config.vm.synced_folder ".", "/vagrant", disabled: true   # Ansible copie le code lui-même

  MACHINES.each do |name, m|
    config.vm.define name do |node|
      node.vm.hostname = name
      node.vm.network "private_network", ip: m["ip"]

      node.vm.provider "vmware_desktop" do |v|
        v.vmx["displayName"] = "insacloud-#{name}"
        v.vmx["memsize"]     = m["memory"].to_s
        v.vmx["numvcpus"]    = m["cpus"].to_s
        v.gui = false
        v.allowlist_verified = true
      end
      node.vm.provider "parallels" do |prl|
        prl.name   = "insacloud-#{name}"
        prl.memory = m["memory"]
        prl.cpus   = m["cpus"]
        prl.update_guest_tools = false
      end
      node.vm.provider "qemu" do |q, override|
        # vagrant-qemu ne gère pas les réseaux privés : SSH est redirigé sur localhost,
        # adaptez l'inventaire avec `vagrant ssh-config`. Les workers doivent rester
        # joignables entre eux -> préférez VMware/Parallels/VirtualBox pour 3 VM.
        override.vm.box = HOST_ARM64 ? "perk/ubuntu-2204-arm64" : "generic/ubuntu2204"
        override.vm.network "private_network", ip: m["ip"], disabled: true
        q.memory  = "#{m['memory']}M"
        q.smp     = m["cpus"]
        q.arch    = HOST_ARM64 ? "aarch64" : "x86_64"
        q.machine = HOST_ARM64 ? "virt,accel=hvf,highmem=on" : "q35,accel=hvf"
        q.cpu     = "host"
      end
      node.vm.provider "virtualbox" do |vb|
        vb.name   = "insacloud-#{name}"
        vb.memory = m["memory"]
        vb.cpus   = m["cpus"]
        vb.gui    = false
      end

      # Pré-requis Ansible : python3 (idempotent)
      node.vm.provision "shell", name: "bootstrap-python", inline: <<-SHELL
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq python3 python3-apt > /dev/null
      SHELL
    end
  end
end
