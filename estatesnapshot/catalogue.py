"""Resource type catalogue: category, friendly name and AzureDiagrammer icon slug.
Unknown types still work; they fall back to a name derived from the type string."""

T = {
    # networking
    "microsoft.network/virtualnetworks": ("Networking", "Virtual Network", None),
    "microsoft.network/virtualnetworks/subnets": ("Networking", "Subnet", None),
    "microsoft.network/networksecuritygroups": ("Networking", "Network Security Group", "nsg"),
    "microsoft.network/routetables": ("Networking", "Route Table", "routetable"),
    "microsoft.network/publicipaddresses": ("Networking", "Public IP Address", None),
    "microsoft.network/networkinterfaces": ("Networking", "Network Interface", None),
    "microsoft.network/privateendpoints": ("Networking", "Private Endpoint", "privateendpoint"),
    "microsoft.network/privatednszones": ("Networking", "Private DNS Zone", "privatedns"),
    "microsoft.network/privatednszones/virtualnetworklinks": ("Networking", "Private DNS Zone Link", None),
    "microsoft.network/dnszones": ("Networking", "DNS Zone", "dns"),
    "microsoft.network/dnsresolvers": ("Networking", "DNS Private Resolver", "dns"),
    "microsoft.network/azurefirewalls": ("Networking", "Azure Firewall", "firewall"),
    "microsoft.network/firewallpolicies": ("Networking", "Firewall Policy", None),
    "microsoft.network/applicationgateways": ("Networking", "Application Gateway", "appgw"),
    "microsoft.network/applicationgatewaywebapplicationfirewallpolicies": ("Networking", "WAF Policy", None),
    "microsoft.network/frontdoorwebapplicationfirewallpolicies": ("Networking", "Front Door WAF Policy", None),
    "microsoft.network/loadbalancers": ("Networking", "Load Balancer", "lb"),
    "microsoft.network/natgateways": ("Networking", "NAT Gateway", "natgw"),
    "microsoft.network/bastionhosts": ("Networking", "Azure Bastion", "bastion"),
    "microsoft.network/virtualnetworkgateways": ("Networking", "Virtual Network Gateway", "vnetgw"),
    "microsoft.network/localnetworkgateways": ("Networking", "Local Network Gateway", None),
    "microsoft.network/connections": ("Networking", "Gateway Connection", None),
    "microsoft.network/expressroutecircuits": ("Networking", "ExpressRoute Circuit", "expressroute"),
    "microsoft.network/virtualwans": ("Networking", "Virtual WAN", None),
    "microsoft.network/virtualhubs": ("Networking", "Virtual Hub", None),
    "microsoft.network/frontdoors": ("Networking", "Front Door (classic)", "frontdoor"),
    "microsoft.cdn/profiles": ("Networking", "Front Door / CDN Profile", "frontdoor"),
    "microsoft.network/trafficmanagerprofiles": ("Networking", "Traffic Manager", "trafficmanager"),
    "microsoft.network/ddosprotectionplans": ("Networking", "DDoS Protection Plan", "ddos"),
    "microsoft.network/networkwatchers": ("Networking", "Network Watcher", None),
    "microsoft.network/networkwatchers/flowlogs": ("Networking", "Flow Log", None),
    "microsoft.network/privatelinkservices": ("Networking", "Private Link Service", None),
    "microsoft.network/ipgroups": ("Networking", "IP Group", None),
    "microsoft.network/applicationsecuritygroups": ("Networking", "Application Security Group", None),
    "microsoft.network/networkmanagers": ("Networking", "Virtual Network Manager", None),
    # compute
    "microsoft.compute/virtualmachines": ("Compute", "Virtual Machine", "vm"),
    "microsoft.compute/virtualmachinescalesets": ("Compute", "VM Scale Set", "vmss"),
    "microsoft.compute/disks": ("Compute", "Managed Disk", None),
    "microsoft.compute/snapshots": ("Compute", "Disk Snapshot", None),
    "microsoft.compute/availabilitysets": ("Compute", "Availability Set", None),
    "microsoft.compute/images": ("Compute", "Image", None),
    "microsoft.compute/galleries": ("Compute", "Compute Gallery", None),
    "microsoft.compute/sshpublickeys": ("Compute", "SSH Key", None),
    "microsoft.compute/virtualmachines/extensions": ("Compute", "VM Extension", None),
    "microsoft.compute/restorepointcollections": ("Compute", "Restore Point Collection", None),
    "microsoft.virtualmachineimages/imagetemplates": ("Compute", "Image Builder Template", "imagetemplate"),
    "microsoft.desktopvirtualization/hostpools": ("Compute", "AVD Host Pool", "avd"),
    "microsoft.desktopvirtualization/applicationgroups": ("Compute", "AVD Application Group", None),
    "microsoft.desktopvirtualization/workspaces": ("Compute", "AVD Workspace", None),
    "microsoft.desktopvirtualization/scalingplans": ("Compute", "AVD Scaling Plan", None),
    "microsoft.hybridcompute/machines": ("Compute", "Arc-enabled Server", None),
    # containers
    "microsoft.containerservice/managedclusters": ("Containers", "AKS Cluster", "aks"),
    "microsoft.containerregistry/registries": ("Containers", "Container Registry", "acr"),
    "microsoft.app/managedenvironments": ("Containers", "Container Apps Environment", "containerapps"),
    "microsoft.app/containerapps": ("Containers", "Container App", "containerapps"),
    "microsoft.app/jobs": ("Containers", "Container Apps Job", "containerapps"),
    "microsoft.containerinstance/containergroups": ("Containers", "Container Instance", "aci"),
    # web
    "microsoft.web/serverfarms": ("Web", "App Service Plan", "appserviceplan"),
    "microsoft.web/sites": ("Web", "App Service", "appservice"),
    "microsoft.web/sites/slots": ("Web", "Deployment Slot", "appservice"),
    "microsoft.web/staticsites": ("Web", "Static Web App", "appservice"),
    "microsoft.web/certificates": ("Web", "App Service Certificate", None),
    "microsoft.web/connections": ("Integration", "API Connection", None),
    "microsoft.apimanagement/service": ("Integration", "API Management", "apim"),
    # data
    "microsoft.sql/servers": ("Data", "Azure SQL Server", "sqlserver"),
    "microsoft.sql/servers/databases": ("Data", "Azure SQL Database", "sqldb"),
    "microsoft.sql/servers/elasticpools": ("Data", "SQL Elastic Pool", "sqldb"),
    "microsoft.sql/managedinstances": ("Data", "SQL Managed Instance", "sqlmi"),
    "microsoft.sql/managedinstances/databases": ("Data", "SQL MI Database", "sqlmi"),
    "microsoft.sqlvirtualmachine/sqlvirtualmachines": ("Data", "SQL Server on VM", "sqlvm"),
    "microsoft.documentdb/databaseaccounts": ("Data", "Cosmos DB", "cosmos"),
    "microsoft.documentdb/mongoclusters": ("Data", "Cosmos DB for MongoDB (vCore)", "cosmos"),
    "microsoft.documentdb/mongoclusters/firewallrules": ("Data", "MongoDB vCore Firewall Rule", None),
    "microsoft.cdn/profiles/afdendpoints": ("Networking", "Front Door Endpoint", None),
    "microsoft.compute/galleries/images": ("Compute", "Gallery Image Definition", None),
    "microsoft.compute/galleries/images/versions": ("Compute", "Gallery Image Version", None),
    "microsoft.devtestlab/schedules": ("Compute", "Auto-shutdown Schedule", None),
    "microsoft.devtestlab/labs": ("Developer", "DevTest Lab", None),
    "microsoft.app/managedenvironments/certificates": ("Containers", "Container Apps Certificate", None),
    "microsoft.monitor/accounts": ("Monitoring & Management", "Azure Monitor Workspace", None),
    "microsoft.portalservices/dashboards": ("Monitoring & Management", "Dashboard", None),
    "microsoft.storagemover/storagemovers": ("Storage", "Storage Mover", None),
    "microsoft.migrate/migrateprojects": ("Monitoring & Management", "Azure Migrate Project", "migrate"),
    "microsoft.migrate/assessmentprojects": ("Monitoring & Management", "Azure Migrate Assessment", "migrate"),
    "microsoft.offazure/serversites": ("Monitoring & Management", "Azure Migrate Discovery Site", None),
    "microsoft.offazure/vmwaresites": ("Monitoring & Management", "Azure Migrate VMware Site", None),
    "microsoft.offazure/hypervsites": ("Monitoring & Management", "Azure Migrate Hyper-V Site", None),
    "microsoft.dbforpostgresql/flexibleservers": ("Data", "PostgreSQL Flexible Server", "postgres"),
    "microsoft.dbformysql/flexibleservers": ("Data", "MySQL Flexible Server", "mysql"),
    "microsoft.cache/redis": ("Data", "Azure Cache for Redis", "redis"),
    "microsoft.cache/redisenterprise": ("Data", "Azure Managed Redis", "redis"),
    "microsoft.datafactory/factories": ("Data", "Data Factory", "datafactory"),
    "microsoft.synapse/workspaces": ("Data", "Synapse Workspace", "synapse"),
    "microsoft.databricks/workspaces": ("Data", "Databricks Workspace", None),
    "microsoft.kusto/clusters": ("Data", "Data Explorer Cluster", None),
    "microsoft.storage/storageaccounts": ("Storage", "Storage Account", "storage"),
    "microsoft.netapp/netappaccounts": ("Storage", "Azure NetApp Files", None),
    "microsoft.storagesync/storagesyncservices": ("Storage", "Storage Sync Service", "files"),
    # integration
    "microsoft.servicebus/namespaces": ("Integration", "Service Bus", "servicebus"),
    "microsoft.eventhub/namespaces": ("Integration", "Event Hubs", "eventhubs"),
    "microsoft.eventgrid/topics": ("Integration", "Event Grid Topic", "eventgrid"),
    "microsoft.eventgrid/systemtopics": ("Integration", "Event Grid System Topic", "eventgrid"),
    "microsoft.eventgrid/domains": ("Integration", "Event Grid Domain", "eventgrid"),
    "microsoft.logic/workflows": ("Integration", "Logic App", "logicapps"),
    "microsoft.signalrservice/signalr": ("Integration", "SignalR Service", None),
    "microsoft.communication/communicationservices": ("Integration", "Communication Services", None),
    "microsoft.maps/accounts": ("Integration", "Azure Maps", None),
    # security and identity
    "microsoft.keyvault/vaults": ("Security & Identity", "Key Vault", "keyvault"),
    "microsoft.managedidentity/userassignedidentities": ("Security & Identity", "Managed Identity", "managedidentity"),
    "microsoft.operationalinsights/workspaces/providers/onboardingstates": ("Security & Identity", "Sentinel", "sentinel"),
    # monitoring and management
    "microsoft.operationalinsights/workspaces": ("Monitoring & Management", "Log Analytics Workspace", "loganalytics"),
    "microsoft.insights/components": ("Monitoring & Management", "Application Insights", "appinsights"),
    "microsoft.insights/actiongroups": ("Monitoring & Management", "Action Group", None),
    "microsoft.insights/metricalerts": ("Monitoring & Management", "Metric Alert", None),
    "microsoft.insights/scheduledqueryrules": ("Monitoring & Management", "Log Alert", None),
    "microsoft.insights/activitylogalerts": ("Monitoring & Management", "Activity Log Alert", None),
    "microsoft.insights/datacollectionrules": ("Monitoring & Management", "Data Collection Rule", None),
    "microsoft.insights/datacollectionendpoints": ("Monitoring & Management", "Data Collection Endpoint", None),
    "microsoft.insights/workbooks": ("Monitoring & Management", "Workbook", None),
    "microsoft.alertsmanagement/smartdetectoralertrules": ("Monitoring & Management", "Smart Detector Alert", None),
    "microsoft.alertsmanagement/actionrules": ("Monitoring & Management", "Alert Processing Rule", None),
    "microsoft.operationsmanagement/solutions": ("Monitoring & Management", "Monitoring Solution", None),
    "microsoft.automation/automationaccounts": ("Monitoring & Management", "Automation Account", "automation"),
    "microsoft.automation/automationaccounts/runbooks": ("Monitoring & Management", "Runbook", None),
    "microsoft.recoveryservices/vaults": ("Monitoring & Management", "Recovery Services Vault", "recovery"),
    "microsoft.dataprotection/backupvaults": ("Monitoring & Management", "Backup Vault", "backup"),
    "microsoft.maintenance/maintenanceconfigurations": ("Monitoring & Management", "Maintenance Configuration", "updatemanager"),
    "microsoft.portal/dashboards": ("Monitoring & Management", "Dashboard", None),
    "microsoft.dashboard/grafana": ("Monitoring & Management", "Managed Grafana", None),
    "microsoft.devcenter/devcenters": ("Developer", "Dev Center", None),
    "microsoft.cognitiveservices/accounts": ("AI", "Azure AI Services", None),
    "microsoft.machinelearningservices/workspaces": ("AI", "Machine Learning Workspace", None),
    "microsoft.search/searchservices": ("AI", "Azure AI Search", None),
}

# Supporting resources: counted, but not listed individually in the brief.
SUPPORTING = {
    "microsoft.network/networkinterfaces", "microsoft.compute/disks", "microsoft.compute/virtualmachines/extensions",
    "microsoft.network/privatednszones/virtualnetworklinks", "microsoft.network/networkwatchers",
    "microsoft.network/networkwatchers/flowlogs", "microsoft.insights/actiongroups", "microsoft.insights/metricalerts",
    "microsoft.insights/scheduledqueryrules", "microsoft.insights/activitylogalerts",
    "microsoft.alertsmanagement/smartdetectoralertrules", "microsoft.alertsmanagement/actionrules",
    "microsoft.insights/datacollectionrules", "microsoft.insights/datacollectionendpoints", "microsoft.insights/workbooks",
    "microsoft.operationsmanagement/solutions", "microsoft.portal/dashboards", "microsoft.compute/snapshots",
    "microsoft.compute/sshpublickeys", "microsoft.compute/restorepointcollections", "microsoft.web/certificates",
    "microsoft.web/connections", "microsoft.automation/automationaccounts/runbooks",
    "microsoft.eventgrid/systemtopics", "microsoft.network/applicationsecuritygroups",
    "microsoft.cdn/profiles/afdendpoints", "microsoft.compute/galleries/images", "microsoft.compute/galleries/images/versions",
    "microsoft.devtestlab/schedules", "microsoft.app/managedenvironments/certificates", "microsoft.portalservices/dashboards",
    "microsoft.documentdb/mongoclusters/firewallrules",
}

CATEGORY_ORDER = ["Networking", "Compute", "Containers", "Web", "Data", "Storage", "Integration",
                  "AI", "Security & Identity", "Monitoring & Management", "Developer", "Other"]


def info(rtype):
    t = (rtype or "").lower()
    if t in T:
        return T[t]
    last = t.split("/")[-1]
    words = []
    for w in last.replace("-", " ").split():
        words.append(w[:1].upper() + w[1:])
    return ("Other", " ".join(words) or t, None)


def friendly(rtype):
    return info(rtype)[1]


def category(rtype):
    return info(rtype)[0]


def icon(rtype):
    return info(rtype)[2]
