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

    # TODEL?
    # no vamos autilizar el hash de odoo porque la estructura no nos sirve
    # para verifactu, por lo que este código no nos terminaría de valer.
    # De momento lo dejamos hasta saber cómo vamos a controlar
    # el tema de la factura anterior enviada a verifactu para el cálculo
    # del hash, y el control de modificaciones en las facturas ya enviadas.
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
    verifactu_enabled = fields.Boolean(string="Enable veri*FACTU", default=True)

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

    """@api.model
            if country_code == "ES" and journal_type == "sale" and verifactu_enabled:
            raise ValidationError(
                _("You can't have a sale journal in Spain with veri*FACTU enabled.")
            )
    def check_hash_modification(self, country_code, journal_type, verifactu_enabled):
        if country_code == "ES" and journal_type == "sale" and verifactu_enabled:
            raise ValidationError(
                _("You can't have a sale journal in Spain with veri*FACTU enabled.")
            )"""
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

    @api.multi
    def create(self, vals_list):
        for vals in vals_list:
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
        return super(account_journal, self).create(vals_list)

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
    # ==== Hash Fields ====
    """restrict_mode_hash_table = fields.Boolean(string="Lock Posted Entries with Hash",
        help="If ticked, the accounting entry or invoice receives a hash as soon as it is posted and cannot be modified anymore.")

    secure_sequence_id = fields.Many2one('ir.sequence',
        help='Sequence to use to ensure the securisation of data',
        check_company=True,
        readonly=True, copy=False)"""

    """@api.multi
    def write(self, vals):
        res = super(account_journal, self).write(vals)
        for record in self:
            if 'restrict_mode_hash_table' in vals and not vals.get('restrict_mode_hash_table'):
                journal_entry = self.env['account.move'].sudo().search([('journal_id', '=', journal.id), ('state', '=', 'posted'), ('secure_sequence_number', '!=', 0)], limit=1)
                if journal_entry:
                    field_string = self._fields['restrict_mode_hash_table'].get_description(self.env)['string']
                    raise UserError(_("You cannot modify the field %s of a journal that already has accounting entries.", field_string))
            if record.restrict_mode_hash_table and not record.secure_sequence_id:
                record._create_secure_sequence(['secure_sequence_id'])
        return res

    @api.model
    def create(self, vals):
        res = super(account_journal, self.with_context(mail_create_nolog=True))).create(vals)
        # Create the secure_sequence_id if necessary
        if res.restrict_mode_hash_table and not res.secure_sequence_id:
            res._create_secure_sequence(['secure_sequence_id'])
        return res"""

    """def _create_secure_sequence(self, sequence_fields):
        for journal in self:
            vals_write = {}
            for seq_field in sequence_fields:
                if not journal[seq_field]:
                    vals = {
                        'name': _('Securisation of %s - %s') % (seq_field, journal.name),
                        'code': 'SECUR%s-%s' % (journal.id, seq_field),
                        'implementation': 'no_gap',
                        'prefix': '',
                        'suffix': '',
                        'padding': 0,
                        'company_id': journal.company_id.id}
                    seq = self.env['ir.sequence'].create(vals)
                    vals_write[seq_field] = seq.id
            if vals_write:
                journal.write(vals_write)"""
