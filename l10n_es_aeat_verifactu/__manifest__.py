# -*- coding: utf-8 -*-
############################################################################
{
    'name' : 'Comunicación Veri*FACTU',
    'version' : '11.0.1.1',
    'author' : 'OpenERP SA',
    'category' : 'Accounting & Finance',
    'description' : """
Comunicación Veri*FACTU
=======================================
Módulo para la presentación inmediata de la facturación.
(Generación de QR y hash en las facturas, envío a Veri*FACTU al confirmar la factura)

Para instalar esté módulo se necesita:

   - Libreria Python Zeep, se puede instalar con el comando 'pip install zeep'
   - Libreria Python Requests, se puede instalar con el comando 'pip install requests'

Para configurar este módulo es necesario:

   - Acceder a Facturación -> Configuración -> VERIFACTU -> Agencia VERIFACTU, podrás consultar las URLs del servicio SOAP de Hacienda. Estas URLs pueden cambiar según comunidades
   - El certificado enviado por la FMNT es en formato p12, este certificado no se puede usar directamente con Zeep. Accede a Facturación -> Configuración -> VERIFACTU -> Certificado VERIFACTU, y allí podrás: Subir el certificado p12 y extraer las claves públicas y privadas con el botón "Obtener claves"
   - Debes tener en cuenta que los certificados se alojan en una carpeta accesible por la instalación de Odoo.
   - Completar los datos de desarrollador a nivel de compañía
   - En caso de que la obtención de claves no funcione y uses Linux, cuentas con los siguientes comandos para tratar de solucionarlo:
     Clave pública: "openssl pkcs12 -in Certificado.p12 -nokeys -out publicCert.crt -nodes"
     Clave privada: "openssl pkcs12 -in Certificado.p12 -nocerts -out privateKey.pem -nodes"
   - Establecer en las posiciones fiscales la clave de impuestos y la clave de registro verifactu.
   - Para aplicar las claves ejecute el asistente de actualización del módulo account_chart_update.
    """,
    'website': 'https://github.com/infobit/verifactu.git',
    "external_dependencies": {"python": ["zeep", "requests"]},
    'depends' : ['account', 'l10n_es', 'mail'], #"account_invoice_refund_link", "l10n_es_aeat"],
    'data': [
        "data/aeat_verifactu_tax_agency_data.xml",
        "data/aeat_verifactu_registration_keys.xml",
        "data/aeat_verifactu_map_data.xml",
        "data/parameters.xml",
        "data/mail_activity_data.xml",
        "data/ir_cron.xml",
        "security/verifactu_security.xml",
        'security/ir.model.access.csv',
        "views/certificate_verifactu_view.xml",
        "views/aeat_tax_agency_view.xml",
        "views/account_fiscal_position_view.xml",
        "views/res_company_view.xml",
        "views/res_partner_view.xml",
        'views/account_journal_view.xml',
        'views/account_invoice_view.xml',
        "views/aeat_verifactu_map_view.xml",
        "views/aeat_verifactu_map_lines_view.xml",
        "views/aeat_verifactu_registration_keys_view.xml",
        "views/verifactu_invoice_entry_response_view.xml",
        "views/verifactu_invoice_entry_view.xml",
        "wizard/aeat_verifactu_password_view.xml",
        "report/report_invoice.xml",
    ],
    'installable': True,
    'auto_install': False,
    "license": "AGPL-3", 
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
