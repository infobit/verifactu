# -*- coding: utf-8 -*-
############################################################################
{
    'name' : 'Comunicación Veri*FACTU',
    'version' : '11.0.1.0',
    'author' : 'OpenERP SA',
    'category' : 'Accounting & Finance',
    'description' : """
Verifactu - Invoice.
====================================

    * Add Hash
    * 
    * 
    """,
    #'website': 'https://www.odoo.com/page/billing',
    'depends' : ['account'], #'queue_job', 'account'],
    'data': [
        "data/aeat_verifactu_tax_agency_data.xml",
        "data/aeat_verifactu_registration_keys.xml",
        "data/aeat_verifactu_map_data.xml",
        "data/parameters.xml",
        "data/ir_cron.xml",
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
        "wizard/aeat_verifactu_password_view.xml",
        "report/report_invoice.xml",
    ],
    'installable': True,
    'auto_install': False,
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
