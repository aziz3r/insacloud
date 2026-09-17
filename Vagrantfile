# -*- mode: ruby -*-
# vi: set ft=ruby :
# =============================================================================
#  Vagrantfile - VM de "production" locale d'InsaCloud
# -----------------------------------------------------------------------------
#  Crée une VM Ubuntu 22.04 joignable sur 192.168.56.10 (réseau privé hôte-VM).
#  C'est cette VM qu'Ansible configure ensuite via 3_ansible/inventaire.ini.
#
#  Fournisseurs supportés :
#    - VirtualBox      (défaut ; Mac Intel, Windows, Linux)
#    - VMware Fusion   (Mac Apple Silicon)  -> vagrant up --provider=vmware_desktop
#  La box "bento/ubuntu-22.04" existe en amd64 ET arm64, donc le même fichier
#  fonctionne sur les deux architectures.
#
#  Commandes utiles :
#    vagrant up                # crée et démarre la VM
#    vagrant ssh               # shell dans la VM
#    vagrant provision         # relance Ansible seul (idempotence)
#    vagrant halt / destroy -f # arrêt / suppression
# =============================================================================

VM_IP       = "192.168.56.10"
VM_NAME     = "insacloud"
VM_MEMORY   = 4096   # les machines "bureau" prennent 1 Go chacune
VM_CPUS     = 2
BOX         = "bento/ubuntu-22.04"

Vagrant.configure("2") do |config|
  config.vm.box      = BOX
  config.vm.hostname = VM_NAME

  # Réseau privé : la VM est joignable depuis le Mac sur 192.168.56.10.
  # Le site web sera donc sur http://192.168.56.10:5000 et les machines
  # louées sur ssh root@192.168.56.10 -p <port>.
  config.vm.network "private_network", ip: VM_IP

  # Pas de dossier partagé : Ansible copie lui-même le code (déploiement réel).
  config.vm.synced_folder ".", "/vagrant", disabled: true

  # ---- VirtualBox ------------------------------------------------------------
  config.vm.provider "virtualbox" do |vb|
    vb.name   = VM_NAME
    vb.memory = VM_MEMORY
    vb.cpus   = VM_CPUS
    vb.gui    = false
  end

  # ---- VMware Fusion / Workstation (plugin vagrant-vmware-desktop) ----------
  config.vm.provider "vmware_desktop" do |v|
    v.vmx["displayName"] = VM_NAME
    v.vmx["memsize"]     = VM_MEMORY.to_s
    v.vmx["numvcpus"]    = VM_CPUS.to_s
    v.gui = false
  end

  # ---- Pré-requis minimal pour Ansible : python3 présent sur la cible -------
  # (idempotent : apt ne réinstalle pas un paquet déjà présent)
  config.vm.provision "shell", name: "bootstrap-python", inline: <<-SHELL
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-apt > /dev/null
  SHELL

  # ---- Provisionnement Ansible ---------------------------------------------
  # Vagrant lance le playbook depuis le Mac avec l'inventaire du projet.
  # Décommentez si Ansible est installé sur le poste ; sinon lancez à la main :
  #   ansible-playbook -i 3_ansible/inventaire.ini 3_ansible/site.yml
  #
  # config.vm.provision "ansible" do |ansible|
  #   ansible.playbook          = "3_ansible/site.yml"
  #   ansible.inventory_path    = "3_ansible/inventaire.ini"
  #   ansible.limit             = "all"
  #   ansible.compatibility_mode = "2.0"
  # end
end
