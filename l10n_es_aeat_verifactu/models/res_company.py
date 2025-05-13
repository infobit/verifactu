# -*- coding: utf-8 -*-
##############################################################################
import itertools
from lxml import etree

import logging

from openerp import models, fields, api, _
from openerp.exceptions import except_orm, Warning, RedirectWarning
from openerp.tools import float_compare
import openerp.addons.decimal_precision as dp
from openerp.tools import ormcache
_logger = logging.getLogger(__name__)

class res_company(models.Model):
    _inherit = "res.company"

    tax_agency_id = fields.Many2one("aeat.tax.agency", "AEAT Agency")
    verifactu_enabled = fields.Boolean(string="Enable veri*FACTU")
    verifactu_test = fields.Boolean(string="Is it the veri*FACTU test environment?")
    verifactu_description = fields.Text(default="/", size=500, help="The description for Verifactu invoices if not set",)
    url_qrverifactu_test = fields.Char(string="Url QR Verifactu Pruebas", default="https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?")
    url_qrverifactu = fields.Char(string="Url QR Verifactu", default="https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR?")
    url_qrnoverifactu_test = fields.Char(string="Url QR No Verifactu", default="https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?")
    url_qrnoverifactu = fields.Char(string="Url QR No Verifactu", default="https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?")
    verifactu_last_document_id = fields.Reference(
        string="Last Verifactu Document",
        selection="_selection_verifactu_reference_models",
        #readonly=True,
    )

    @api.model
    def _selection_verifactu_reference_models(self):
        return self.env["account.invoice"]._selection_verifactu_reference_models()

    def get_taxes_from_templates(self, tax_templates):
        """Return company taxes that match the given tax templates."""
        self.ensure_one()
        tax_ids = []
        # We need to rebrowse the records to avoid a problem with the ormcache
        # and virtual records that populate m2m as NewId.
        for tmpl in self.env["account.tax.template"].browse(tax_templates.ids):
            tax_id = self._get_tax_id_from_tax_template(tmpl, self)
            if tax_id:
                tax_ids.append(tax_id)
        return self.env["account.tax"].browse(tax_ids)

    @ormcache("tax_template", "company")
    def _get_tax_id_from_tax_template(self, tax_template, company):
        """Low level cached search for a tax given its tax template and
        company.
        """
        xmlids = (
            self.sudo()
            .env["ir.model.data"]
            .search_read(
                [
                    ("model", "=", "account.tax.template"),
                    ("res_id", "=", tax_template.id),
                ],
                ["name", "module"],
            )
        )
        return (
            xmlids
            and self.sudo()
            .env["ir.model.data"]
            .search(
                [
                    ("model", "=", "account.tax"),
                    ("module", "=", xmlids[0]["module"]),
                    ("name", "=", "{}_{}".format(company.id, xmlids[0]["name"])),
                ]
            )
            .res_id
            or False
        )
