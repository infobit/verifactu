# -*- coding: utf-8 -*-
# (c) 2017 Diagram Software S.L.
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

from openerp import api, models, fields, _, SUPERUSER_ID


class CertificateVerifactu(models.Model):
    _name = 'certificate.verifactu'

    name = fields.Char(string="Name")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active')
    ], string="State", default="draft")
    file = fields.Binary(string="File", required=True)
    folder = fields.Char(string="Folder Name")
    date_start = fields.Date(string="Start Date")
    date_end = fields.Date(string="End Date", required=True)
    public_key = fields.Char(string="Public Key", readonly=True)
    private_key = fields.Char(string="Private Key", readonly=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.user.company_id.id
    )

    @api.multi
    def load_password_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Insert Password'),
            'res_model': 'l10n.es.aeat.verifactu.password',
            'view_mode': 'form',
            'view_type': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }

    @api.multi
    def action_active(self):
        self.ensure_one()
        self.search([
            ('id', '!=', self.id),
            ('company_id', '=', self.company_id.id),
        ]).write({'state': 'draft'})
        self.state = 'active'
        if self.public_key:
	    verifactu_crt = self.env['ir.config_parameter'].search([('key','=','l10n_es_aeat_verifactu.publicCrt')])
            #sii_crt = self.env.ref('l10n_es_aeat_verifactu.publicCrt')
            verifactu_crt.sudo().write({'value': self.public_key})
        if self.private_key:
	    verifactu_key = self.env['ir.config_parameter'].search([('key','=','l10n_es_aeat_verifactu.privateKey')])
            #sii_key = self.env.ref('l10n_es_aeat_verifactu.privateKey')
            verifactu_key.sudo().write({'value': self.private_key})
        other_configs = self.search([('id', '!=', self.id)])
        for config_id in other_configs:
            config_id.sudo().state = 'draft'
        self.state = 'active'
