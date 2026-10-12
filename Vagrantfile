# -*- mode: ruby -*-
# vi: set ft=ruby :
# =============================================================================
#  Vagrantfile - Infrastructure InsaCloud : 1 contrôleur + 2 workers
# -----------------------------------------------------------------------------
#    controller  192.168.56.10  site web (nginx + Flask/Gunicorn), Faucheur, watchdog
#    worker1     192.168.56.11  Docker : héberge les machines louées
#    worker2     192.168.56.12  Docker : héberge les machines louées
#    worker3     192.168.56.13  Docker : héberge les machines louées
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
#    vagrant up                     # les 4 VM (1 contrôleur + 3 workers)
#    WORKERS=2 vagrant up           # parc réduit sur un poste modeste
#    vagrant up controller worker1  # un sous-ensemble
#    vagrant status / ssh worker1 / halt / destroy -f
#  Puis : cd 3_ansible && ansible-playbook site.yml --ask-vault-pass
# =============================================================================

BOX = "bento/ubuntu-22.04"   # existe en amd64 et arm64 (VMware, Parallels, VirtualBox)

# Trois workers, comme le demande le cahier des charges. Sur un poste modeste,
# WORKERS=2 (ou 1) réduit le parc sans toucher au fichier :
#   WORKERS=2 vagrant up
NB_WORKERS = (ENV["WORKERS"] || "3").to_i.clamp(1, 9)

# Taille des VM. Les valeurs par défaut sont confortables ; sur un poste de 8 Go
# elles ne tiennent pas (2 + 3 x 3 = 11 Go). Elles sont donc réglables sans
# toucher au fichier, pour pouvoir démontrer la reprise sur panne — qui exige
# un contrôleur et DEUX workers — sur une machine modeste :
#
#   WORKERS=2 CTRL_MEM=1536 WORKER_MEM=1280 vagrant up
#
# Le contrôleur porte nginx, Gunicorn et la base ; un worker ne porte que
# Docker et les conteneurs loués, il se contente donc de moins.
CTRL_MEM   = (ENV["CTRL_MEM"]   || "2048").to_i.clamp(1024, 16384)
WORKER_MEM = (ENV["WORKER_MEM"] || "3072").to_i.clamp(768, 16384)
CTRL_CPUS   = (ENV["CTRL_CPUS"]   || "2").to_i.clamp(1, 8)
WORKER_CPUS = (ENV["WORKER_CPUS"] || "2").to_i.clamp(1, 8)

MACHINES = {
  "controller" => { "ip" => "192.168.56.10", "memory" => CTRL_MEM, "cpus" => CTRL_CPUS },
}
(1..NB_WORKERS).each do |n|
  MACHINES["worker#{n}"] = { "ip" => "192.168.56.#{10 + n}",
                             "memory" => WORKER_MEM, "cpus" => WORKER_CPUS }
end

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

      # Provisionnement déclaratif : le playbook est joué une fois les trois VM
      # créées (ansible.limit = "all" + parallélisme). `vagrant up` suffit donc
      # à obtenir une plateforme complète. Pour rejouer : `vagrant provision`.
      if name == MACHINES.keys.to_a.last
        node.vm.provision "ansible", name: "insacloud" do |ansible|
          ansible.playbook           = "3_ansible/site.yml"
          ansible.inventory_path     = "3_ansible/inventaire.ini"
          ansible.limit              = "all"
          ansible.compatibility_mode = "2.0"
          ansible.ask_vault_pass     = true
        end
      end
    end
  end
end
