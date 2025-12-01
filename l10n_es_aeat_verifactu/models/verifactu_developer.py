# Copyright 2024 Aures TIC - Almudena de La Puente <almudena@aurestic.es>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openerp import fields, models


class VerifactuDeveloper(models.Model):
    _name = "verifactu.developer"

    name = fields.Char(string="Developer Name", required=True, tracking=True)
    vat = fields.Char(string="Developer VAT", required=True, tracking=True)
    sif_name = fields.Char("SIF Name", required=True, tracking=True)
    sif_id = fields.Char(string="SIF ID", required=True, tracking=True)
    version = fields.Char(default="1.0", required=True, tracking=True)
    installation_number = fields.Integer(default=1, required=True, tracking=True)
    responsibility_declaration = fields.Binary(
        attachment=True, copy=False, tracking=True
    )
    last_verifactu_invoice_entry_id = fields.Many2one(
        "verifactu.invoice.entry",
        string="VeriFactu Invoice Entry",
        #readonly=True,
        copy=False,
    )
