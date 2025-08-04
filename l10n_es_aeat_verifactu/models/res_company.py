# -*- coding: utf-8 -*-
##############################################################################
import itertools
from lxml import etree

import logging

from odoo import models, fields, api, _
from odoo.exceptions import except_orm, Warning, RedirectWarning
from odoo.tools import float_compare
import odoo.addons.decimal_precision as dp
from odoo.tools import ormcache
_logger = logging.getLogger(__name__)

class res_company(models.Model):
    _inherit = "res.company"

    tax_agency_id = fields.Many2one("aeat.tax.agency", "AEAT Agency")
    verifactu_enabled = fields.Boolean(string="Enable veri*FACTU")
    verifactu_test = fields.Boolean(string="Is it the veri*FACTU test environment?")
    verifactu_description = fields.Text(default="/", size=500, help="The description for Verifactu invoices if not set",)
    verifactu_last_document_id = fields.Reference(
        string="Last Verifactu Document",
        selection="_selection_verifactu_reference_models",
        #readonly=True,
    )
    last_verifactu_invoice_entry_id = fields.Many2one(
        "verifactu.invoice.entry",
        string="VeriFactu Invoice Entry",
        #readonly=True,
        copy=False,
    )
    verifactu_developer_id = fields.Many2one(
        comodel_name="verifactu.developer",
        string="Verifactu Developer",
        ondelete="set null",
    )
    verifactu_start_date = fields.Date(
        help="If this field is set, the verifactu won't be enabled on invoices with lower "
        "invoice date. If not set, the verifactu can be enabled on all invoice dates"
    )
    verifactu_use_connector = fields.Boolean(
        string='Use connector',
        help="Check it to use connector instead of sending the invoice "
             "directly when it's validated")
    verifactu_method = fields.Selection(
        string='Method',
        selection=[('auto', 'Automatic'), ('manual', 'Manual')],
        default='auto',
        help="By default, the invoice is sent/queued in validation process. "
             "With manual method, there's a button to send the invoice.")
    verifactu_send_mode = fields.Selection(
        string="Send mode",
        selection=[
            ('auto', 'On validate'),
            ('fixed', 'At fixed time'),
            ('delayed', 'With delay'),
        ], default='auto',
    )
    verifactu_sent_time = fields.Float(string="Sent time")
    verifactu_delay_time = fields.Float(string="Delay time")

    def _get_verifactu_eta(self):
        if self.verifactu_send_mode == 'fixed':
            tz = self.env.context.get('tz', self.env.user.partner_id.tz)
            offset = datetime.now(pytz.timezone(tz)).strftime('%z') if tz \
                else '+00'
            hour_diff = int(offset[:3])
            hour, minute = divmod(self.sent_time * 60, 60)
            hour = int(hour - hour_diff)
            minute = int(minute)
            now = datetime.now()
            if now.hour > hour or (now.hour == hour and now.minute > minute):
                now += timedelta(days=1)
            now = now.replace(hour=hour, minute=minute)
            return now
        elif self.verifactu_send_mode == 'delayed':
            return datetime.now() + timedelta(seconds=self.delay_time * 3600)
        else:
            return None

    @api.model
    def _selection_verifactu_reference_models(self):
        return self.env["account.invoice"]._selection_verifactu_reference_models()


    def write(self, vals):
        res = super(res_company, self).write(vals)
        if "verifactu_enabled" in vals:
            for company in self:
                if vals.get("verifactu_enabled", False):
                    journals = self.env["account.journal"].search(
                        [
                            ("company_id", "=", company.id),
                            ("type", "=", "sale"),
                        ]
                    )
                    if journals:
                        journals.write({"verifactu_enabled": True})
        return res

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
