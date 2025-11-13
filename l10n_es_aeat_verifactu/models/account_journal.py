# -*- coding: utf-8 -*-
##############################################################################
import itertools
from lxml import etree
from odoo import models, fields, api, _
from odoo.exceptions import except_orm, Warning, RedirectWarning
from odoo.tools import float_compare
import odoo.addons.decimal_precision as dp


class account_journal(models.Model):
    _inherit = "account.journal"

    restrict_mode_hash_table = fields.Boolean(
        compute="_compute_restrict_mode_hash_table",
        store=True,
        readonly=False,
    )

    restrict_mode_hash_table_readonly = fields.Boolean(
        store=True,
        compute="_compute_restrict_mode_hash_table",
    )
    company_verifactu_enabled = fields.Boolean(
        related="company_id.verifactu_enabled", string="Company veri*FACTU"
    )
    verifactu_enabled = fields.Boolean(string="Enable veri*FACTU", default=False)

    @api.depends(
        "company_id", "company_id.verifactu_enabled", "verifactu_enabled", "type"
    )
    def _compute_restrict_mode_hash_table(self):
        for record in self:
            record.restrict_mode_hash_table_readonly = False
            if (
                record.company_id.verifactu_enabled
                and record.verifactu_enabled
                and record.type == "sale"
            ):
                record.restrict_mode_hash_table = True
                record.restrict_mode_hash_table_readonly = True

    @api.model
    def check_hash_modification(
        self, verifactu_enabled, journal_type, company_verifactu_enabled
    ):
        if verifactu_enabled and journal_type == "sale" and company_verifactu_enabled:
            raise ValidationError(
                _(
                    "You can't have a sale journal with veri*FACTU enabled"
                    "and not restricted hash modification."
                )
            )

    @api.model
    def create(self, vals):
        #for vals in vals_list:
        if (
                "restrict_mode_hash_table" in vals
                and not vals["restrict_mode_hash_table"]
        ):
                company = self.env["res.company"].browse(vals.get("company_id"))
                self.check_hash_modification(
                  vals.get("verifactu_enabled"),
                  vals.get("type"),
                  company.verifactu_enabled,
                )
        return super(account_journal, self).create(vals)

    @api.multi
    def write(self, vals):
        if "restrict_mode_hash_table" in vals and not vals["restrict_mode_hash_table"]:
            for record in self:
                new_company_id = vals.get("company_id", record.company_id.id)
                new_company = self.env["res.company"].browse(new_company_id)
                new_type = vals.get("type", record.type)
                #new_country_code = new_company.country_code
                #new_verifactu_enabled = new_company.verifactu_enabled
                new_verifactu_enabled = vals.get(
                    "verifactu_enabled", record.verifactu_enabled
                )
                new_company_verifactu_enabled = new_company.verifactu_enabled
                record.check_hash_modification(
                    new_verifactu_enabled, new_type, new_company_verifactu_enabled
                    #new_country_code, new_type, new_verifactu_enabled
                )
        return super(account_journal, self).write(vals)
