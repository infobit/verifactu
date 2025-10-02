# -*- coding: utf-8 -*-
##############################################################################
import itertools
from lxml import etree
from odoo import models, fields, api, _, SUPERUSER_ID, exceptions
from odoo.exceptions import except_orm, Warning, RedirectWarning, ValidationError, UserError
from odoo.tools import float_compare, ustr, float_round, float_compare
import odoo.addons.decimal_precision as dp
from hashlib import sha256
from json import dumps
from odoo.modules.registry import Registry
import logging
import json
import pytz
from requests import Session
_logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
try:
   from zeep import Client, Settings
   from zeep.plugins import HistoryPlugin
   from zeep.transports import Transport
except (ImportError, IOError) as err:
    _logger.debug(err)
#from urlparse import urlparse #urlencode
from base64 import b64encode, b64decode
import qrcode
#from cStringIO import StringIO
from urllib.parse import urlparse
#from io import StringIO
import io
#from cStringIO import StringIO
import psycopg2

#VARIABLES VERIFACTU
####################
VERIFACTU_VERSION = 1.0
VERIFACTU_DATE_FORMAT = "%d-%m-%Y"
VERIFACTU_MACRODATA_LIMIT = 100000000.0
VERIFACTU_STATES = [
    ('not_sent', 'Not sent'),
    ('sent', 'Sent'),
    ('sent_w_errors', 'Accepted with errors'),
    ('sent_modified', 'Registered but last modifications not sent'),
    ('cancelled', 'Cancelled'),
    ('cancelled_modified', 'Cancelled but last modifications not sent'),
]
#VARIABLES HASH FACTURA
#######################
#forbidden fields
INTEGRITY_HASH_MOVE_FIELDS = ('date_invoice', 'journal_id', 'company_id')
INTEGRITY_HASH_LINE_FIELDS = ('price_subtotal', 'price_subtotal', 'account_id', 'partner_id')
VERIFACTU_VALID_INVOICE_STATES = ["open", "paid"]



class account_invoice(models.Model):
    _inherit = "account.invoice"

    verifactu_enabled = fields.Boolean(
        string="Enable AEAT",
        compute="_compute_verifactu_enabled",
    )
    verifactu_send_failed = fields.Boolean("Send failed", readonly=True)
    verifactu_send_error = fields.Text("Send error", readonly=True, copy=False)
    verifactu_header_sent = fields.Text(
        string="VERIFACTU last header sent", copy=False, readonly=True,
    )
    verifactu_content_sent = fields.Text(
        string="VERIFACTU last content sent", copy=False, readonly=True,
    )

    verifactu_hash_string = fields.Text("Verifactu HASH String", copy=False, tracking=True)
    verifactu_hash = fields.Char("Verifactu HASH", copy=False, tracking=True) 
    verifactu_qr_url = fields.Char(string="Verifactu QR URL", copy=False)
    qr_image = fields.Binary("Image QRL Invoice", copy=False)

    verifactu_refund_type = fields.Selection(
        selection=[
            #('S', 'By substitution'),
            ("I", "By differences"),
        ],
        compute="_compute_verifactu_refund_type",
        store=True,
        readonly=False,
    )
    verifactu_description = fields.Text(
        copy=False, readonly=True
    )
    verifactu_macrodata = fields.Boolean(
        string="MacroData",
        help="Check to confirm that the document has an absolute amount "
        "greater o equal to 100 000 000,00 euros.",
        compute="_compute_verifactu_macrodata",
    )
    verifactu_csv = fields.Char(copy=False, readonly=True)
    verifactu_return = fields.Text(copy=False, readonly=True)
    verifactu_registration_key = fields.Many2one(
        comodel_name="aeat.verifactu.registration.keys",
        compute="_compute_verifactu_registration_key",
        store=True,
        readonly=False,
    )
    verifactu_tax_key = fields.Selection(
        string="Verifactu tax key",
        selection="_get_verifactu_tax_keys",
        compute="_compute_verifactu_tax_key",
        store=True,
        readonly=False,
    )
    verifactu_registration_key_code = fields.Char(
        compute="_compute_verifactu_registration_key_code",
        readonly=True,
        string="Verifactu Code",
    )

    verifactu_refund_specific_invoice_type = fields.Selection(
        selection=[
            (
                "R1",
                _("FACTURA RECTIFICATIVA (Art 80.1 y 80.2 y error fundado en derecho)"),
            ),
            ("R2", _("FACTURA RECTIFICATIVA (Art. 80.3)")),
            ("R3", _("FACTURA RECTIFICATIVA (Art. 80.4)")),
            ("R4", _("FACTURA RECTIFICATIVA (Resto)")),
            ("R5", _("FACTURA RECTIFICATIVA EN FACTURAS SIMPLIFICADAS")),
        ],
        help="Fill this field when the refund are one of the specific cases"
        " of article 80 of LIVA for notifying to Vertifactu with the proper"
        " invoice type.",
    )

    verifactu_state = fields.Selection(
        selection=VERIFACTU_STATES, string="VERIFACTU send state", default='not_sent',
        readonly=True, copy=False,
        help="Indicates the state of this invoice in relation with the "
             "presentation at the VERIFACTU",
    )

    verifactu_previous_document_id = fields.Reference(
        string="Previous Verifactu Document",
        selection="_selection_verifactu_reference_models",
        readonly=True,
        copy=False,
    )
    verifactu_next_document_id = fields.Reference(
        string="Next Verifactu Document",
        selection="_selection_verifactu_reference_models",
        readonly=True,
        copy=False,
    )
    verifactu_send_date = fields.Datetime(index=True, copy=False)
    verifactu_registration_date = fields.Datetime(copy=False)
    verifactu_invoice_entry_ids = fields.One2many(
        "verifactu.invoice.entry",
        inverse_name="document_id",
        domain=lambda doc: [("model", "=", doc._name)],
        string="VeriFactu Invoice Entry",
        readonly=True,
        copy=False,
    )
    verifactu_response_line_ids = fields.One2many(
        "verifactu.invoice.entry.response.line",
        inverse_name="document_id",
        domain=lambda doc: [("model", "=", doc._name)],
        string="Verifactu Response Lines",
        readonly=True,
        copy=False,
    )
    last_verifactu_invoice_entry_id = fields.Many2one(
        "verifactu.invoice.entry",
        string="VeriFactu Invoice Entry",
        readonly=True,
        copy=False,
    )
    last_verifactu_response_line_id = fields.Many2one(
        "verifactu.invoice.entry.response.line",
        string="Verifactu Response Line",
        readonly=True,
        copy=False,
    )

    @api.multi
    def action_invoice_cancel(self):
        if self.filtered(lambda inv: inv.state not in ['draft', 'proforma'] and inv.verifactu_enabled):
            raise UserError(_("Esta factura no se puede cancelar, ni modificar"))
        return self.action_cancel()

    """@api.multi
    def action_cancel(self):
        res = super(account_invoice, self).action_cancel()
        if self.state not in ['draft', 'proforma'] and self.verifactu_enabled:
           raise UserError(_("La factura no se puede cancelar, ni modificar"))
           return
        else:
           return res"""


    @api.model
    def _selection_verifactu_reference_models(self):
        # this method is used to define the models that can be used as
        # previous documents in the verifactu mixin
        # it can be inherited to add others models if needed like pos.order
        return [("account.invoice", "Invoice")]

    @api.multi
    def invoice_validate(self):
        res = super(account_invoice, self).invoice_validate() #action_number()
        for record in self:
           if record.verifactu_enabled and record.verifactu_state == "not_sent":
                record._check_verifactu_configuration()
                record.verifactu_registration_date = datetime.now()
                record._generate_verifactu_chaining()
                #LLAMADA A LA GENERACIÓN DEL HASH Y QR
                record._compute_verifactu_qr_url()
        return res

    #boton de envío en la vista de la factura
    @api.multi
    def resend_verifactu(self):
        for rec in self:
            if (
                rec.verifactu_state == "sent_w_errors"
                and rec.last_verifactu_invoice_entry_id
                and not rec.last_verifactu_invoice_entry_id.send_state == "not_sent"
            ):
                rec.verifactu_registration_date = datetime.now()
                rec._generate_verifactu_chaining(entry_type="modify")

    def _check_verifactu_configuration(self):
        if not self.company_id.tax_agency_id:
            raise UserError(
                _(
                    "The document %s cannot be sent to Verifactu because your "
                    "company does not have a tax agency configured."
                )
                % self.name
            )
        if not self.company_id.verifactu_developer_id:
            raise UserError(
                _(
                    "The document %s cannot be sent to Verifactu because your "
                    "company does not have a verifactu developer configured."
                )
                % self.name
            )
        if not self.company_id.country_id or (self.company_id.country_id and self.company_id.country_id.code != "ES"):
            raise UserError(
                _(
                    "The document %s cannot be sent to Verifactu because your "
                    "company is not registered in Spain."
                )
                % self.name
            )
        if not self.fiscal_position_id:
            raise UserError(
                _(
                    "The invoice %s cannot be sent to Verifactu because it "
                    "does not have a fiscal position."
                )
                % self.name
            )
        if not self.verifactu_tax_key:
            raise UserError(
                _(
                    "The invoice %s cannot be sent to Verifactu because it "
                    "does not have a tax key."
                )
                % self.name
            )
        if not self.verifactu_registration_key:
            raise UserError(
                _(
                    "The invoice %s cannot be sent to Verifactu because it "
                    "does not have a registration key."
                )
                % self.name
            )

        if not self._check_all_taxes_mapped():
            raise UserError(
                _(
                    "The invoice %s cannot be sent to Verifactu because it "
                    "does not have all taxes mapped."
                )
                % self.name
            )
        if self.date_invoice > fields.Datetime.now():
            raise UserError(
                _(
                    "La factura  %s no puede ser validada "
                    "porque tiene fecha factura superior a la actual."
                )
                % self.name
            )
        if not self.partner_id.vat:
            raise UserError(
                _(
                    "The document %s cannot be sent to Verifactu because your "
                    "partner does not vat assigned."
                )
                % self.name
            )
        return

    def _check_all_taxes_mapped(self):
        tax_lines = self.tax_line_ids
        if not tax_lines:
            raise UserError(
                _(
                    "The invoice %s cannot be sent to Verifactu because"
                    "it does not have any taxes."
                )
                % self.name
            )
        document_date = self._get_document_fiscal_date()
        verifactu_map = verifactu_map = self._get_verifactu_map(document_date)
        tax_templates = verifactu_map.map_lines.mapped("taxes")
        mapped_taxes = self.company_id.get_taxes_from_templates(tax_templates)
        tax_lines =  self.tax_line_ids
        for tax_line in tax_lines:
            if tax_line["tax_id"] not in mapped_taxes:
                return False
        return True

    @api.model
    def _get_verifactu_map(self, date):
        return (
            self.env["aeat.verifactu.map"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    "|",
                    ("date_from", "<=", date),
                    ("date_from", "=", False),
                    "|",
                    ("date_to", ">=", date),
                    ("date_to", "=", False),
                ],
                limit=1,
            )
        )

    def _generate_verifactu_chaining(self, entry_type=False):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT last_verifactu_invoice_entry_id FROM"
                    " res_company WHERE id = %s FOR UPDATE NOWAIT",
                    [self.company_id.id],
                )
                result = self.env.cr.fetchone()
                previous_invoice_entry_id = result[0] if result and result[0] else False

                #crear registro en invoice entry y asignarlo en 
                invoice_vals = {
                    #"verifactu_chaining_id": chaining.id,
                    "model": self._name,
                    "document_id": self.id,
                    "document_name": self.name,
                    "company_id": self.company_id.id,
                    "document_hash": "",
                    "previous_invoice_entry_id": previous_invoice_entry_id,
                }
                if entry_type:
                    invoice_vals["entry_type"] = entry_type
                invoice_entry = self.env["verifactu.invoice.entry"].create(invoice_vals)
                self.last_verifactu_invoice_entry_id = invoice_entry

                verifactu_hash_values = self._get_verifactu_hash_string()
                self.verifactu_hash_string = verifactu_hash_values
                hash_string = sha256(verifactu_hash_values.encode("utf-8"))
                self.verifactu_hash = hash_string.hexdigest().upper()
                # Generate JSON data for AEAT
                inv_dict = self._get_verifactu_invoice_dict()
                invoice_entry.document_hash = hash_string.hexdigest().upper()
                invoice_entry.aeat_json_data = json.dumps(inv_dict, indent=4)
                self.env.cr.execute(
                    "UPDATE res_company SET "
                    "last_verifactu_invoice_entry_id = %s"
                    "WHERE id = %s",
                    [invoice_entry.id, self.company_id.id],
                )
        except psycopg2.OperationalError as err:
            if err.pgcode == "55P03":  # could not obtain the lock
                raise UserError(
                    _("Could not obtain last document sent to verifactu.")
                ) from err
            raise


    @api.depends("type")
    def _compute_verifactu_refund_type(self):
        for record in self:
            if record.type == "out_refund":
                record.verifactu_refund_type = "I"
            else:
                record.verifactu_refund_type = False

    @api.depends("amount_total")
    def _compute_verifactu_macrodata(self):
        return super()._compute_verifactu_macrodata()

    @api.depends(
        "company_id",
        "company_id.verifactu_enabled",
        "company_id.verifactu_start_date",
        "date_invoice",
        "type",
        "fiscal_position_id",
        "fiscal_position_id.verifactu_active",
        "journal_id",
        "journal_id.verifactu_enabled", 
   )
    def _compute_verifactu_enabled(self):
        """Compute if the invoice is enabled for the veri*FACTU"""
        for invoice in self:
            if (
                invoice.company_id.verifactu_enabled 
                and invoice.journal_id.verifactu_enabled
               ) and (
                not invoice.company_id.verifactu_start_date 
                or (invoice.date_invoice and invoice.company_id.verifactu_start_date and invoice.date_invoice >= invoice.company_id.verifactu_start_date)
               ) and (invoice.type in ["out_invoice", "out_refund"]):
                invoice.verifactu_enabled = (
                    invoice.fiscal_position_id
                    and invoice.fiscal_position_id.verifactu_active
                ) or not invoice.fiscal_position_id
            else:
                invoice.verifactu_enabled = False

    def _get_verifactu_document_type(self):
        invoice_type = ""
        if self.type in ["out_invoice", "out_refund"]:
            is_simplified = self._is_aeat_simplified_invoice()
            invoice_type = "F2" if is_simplified else "F1"
            if self.type == "out_refund":
                if self.verifactu_refund_specific_invoice_type:
                    invoice_type = self.verifactu_refund_specific_invoice_type
                else:
                    invoice_type = "R5" if is_simplified else "R1"
        return invoice_type

    def _get_verifactu_description(self):
        return self.verifactu_description or self.company_id.verifactu_description

    def _get_document_date(self):
        return self.date_invoice

    def _aeat_get_partner(self):
        return self.commercial_partner_id

    def _get_document_fiscal_date(self):
        return self.date_invoice

    def _get_mapping_key(self):
        return self.type

    def _get_verifactu_valid_document_states(self):
        return VERIFACTU_VALID_INVOICE_STATES

    def _get_document_serial_number(self):
        serial_number = (self.number or "")[0:60]
        return serial_number

    def _get_verifactu_issuer(self):
        return self.company_id.partner_id.vat[2:]

    def _get_verifactu_amount_tax(self):
        if self.type == 'out_refund':
           amount_tax = - self.amount_tax
        else:
           amount_tax = self.amount_tax
        return amount_tax #_signed

    def _get_verifactu_amount_total(self):
        return self.amount_total_signed

    def _get_verifactu_previous_hash(self):
        if self.last_verifactu_invoice_entry_id and self.last_verifactu_invoice_entry_id.previous_hash:
           return self.last_verifactu_invoice_entry_id.previous_hash
        else:
           if self.verifactu_previous_document_id:
               return self.verifactu_previous_document_id.verifactu_hash
        return ""

    def _get_verifactu_registration_date(self):
        # Date format must be ISO 8601
        madrid = pytz.timezone('Europe/Madrid')
        dt = datetime.strptime(self.verifactu_registration_date, '%Y-%m-%d %H:%M:%S')
        dt2 = dt + timedelta(hours=2)
        create_date = madrid.localize(dt2)
        iso_date = create_date.isoformat()
        return iso_date

    @api.model
    def _get_verifactu_hash_string(self):
        """Gets the verifactu hash string"""
        if (
            not self.verifactu_enabled
            or self.state == "draft"
            or self.type not in ("out_invoice", "out_refund")
        ):
            return ""
        issuerID = self._get_verifactu_issuer()
        serialNumber = self._get_document_serial_number()
        expeditionDate = self._change_date_format(self._get_document_date())
        documentType = self._get_verifactu_document_type()
        #amountTax = self._get_verifactu_amount_tax()
        #amountTotal = self._get_verifactu_amount_total()
        _taxes_dict, amount_tax, amount_total = self._get_verifactu_taxes_and_total()
        amountTax = round(amount_tax, 2)
        amountTotal = round(amount_total, 2)
        previousHash = self._get_verifactu_previous_hash()
        registrationDate = self._get_verifactu_registration_date()
        verifactu_hash_string = (
            "IDEmisorFactura={}&".format(issuerID) +
            "NumSerieFactura={}&".format(serialNumber) +
            "FechaExpedicionFactura={}&".format(expeditionDate) +
            "TipoFactura={}&".format(documentType) +
            "CuotaTotal={}&".format(amountTax) +
            "ImporteTotal={}&".format(amountTotal) +
            "Huella={}&".format(previousHash) +
            "FechaHoraHusoGenRegistro={}".format(registrationDate)
        )
        return verifactu_hash_string

    @api.model
    def _set_subsanation_verifactu_hash(self):
        verifactu_hash_values = self._get_verifactu_hash_string()
        hash_string = sha256(verifactu_hash_values.encode("utf-8"))
        self.verifactu_hash_string = hash_string
        self.verifactu_hash = hash_string.hexdigest().upper()
        return self.verifactu_hash
        #return hash_string.hexdigest().upper()

    @api.model
    def _get_verifactu_invoice_dict_out(self, cancel=False):
        """Build dict with data to send to AEAT WS for document types:
        out_invoice and out_refund.

        :param cancel: It indicates if the dictionary is for sending a
          cancellation of the document.
        :return: documents (dict) : Dict XML with data for this document.
        """
        #self.ensure_one()
        document_date = self._change_date_format(self._get_document_date())
        company = self.company_id
        serial_number = self._get_document_serial_number()
        #amount_tax = self._get_verifactu_amount_tax()
        #amount_total = self._get_verifactu_amount_total()
        taxes_dict, amount_tax, amount_total = self._get_verifactu_taxes_and_total()
        amountTax = round(amount_tax, 2)
        amountTotal = round(amount_total, 2)
        company_vat = company.partner_id.vat[2:] #_parse_aeat_vat_info()[2]
        verifactu_doc_type = self._get_verifactu_document_type()
        registroAlta = {}
        inv_dict = {
            "IDVersion": self._get_verifactu_version(),
            "IDFactura": {
                "IDEmisorFactura": company_vat,
                "NumSerieFactura": serial_number,
                "FechaExpedicionFactura": document_date,
            },
            "NombreRazonEmisor": self.company_id.name[0:120],
            "TipoFactura": verifactu_doc_type,
        }
        if self.type == "out_refund":
            inv_dict["TipoRectificativa"] = self.verifactu_refund_type
            if self.verifactu_refund_type == "I":
                #inv_dict["FacturasRectificadas"] = []
                origin = self.refund_invoice_id
                if origin:
                    inv_dict["FacturasRectificadas"] = []
                    orig_document_date = self._change_date_format(
                        origin._get_document_date()
                    )
                    orig_serial_number = origin._get_document_serial_number()
                    origin_data = {
                        "IDFacturaRectificada": {
                            "IDEmisorFactura": company_vat,
                            "NumSerieFactura": orig_serial_number,
                            "FechaExpedicionFactura": orig_document_date,
                        }
                    }
                    inv_dict["FacturasRectificadas"].append(origin_data)
                # inv_dict["ImporteRectificacion"] = {
                #     "BaseRectificada": abs(origin.amount_untaxed_signed),
                #     "CuotaRectificada": abs(
                #         origin.amount_total_signed - origin.amount_untaxed_signed
                #     ),
                # }
        inv_dict.update(
            {
                "DescripcionOperacion": self._get_verifactu_description(),
            }
        )
        if verifactu_doc_type not in ("F2", "R5"):
            inv_dict.update(
                {
                    "Destinatarios": self._get_verifactu_receiver_dict(),
                }
            )
        elif verifactu_doc_type in ("F2", "R5"):
            inv_dict.update({"FacturaSinIdentifDestinatarioArt61d": "S"})
        #registrationDate = self._get_verifactu_registration_date()
        inv_dict.update(
            {
                "Desglose": taxes_dict,
                "CuotaTotal": amountTax,
                "ImporteTotal": amountTotal,
                "Encadenamiento": self._get_chaining_invoice_dict(),
                "SistemaInformatico": self._get_verifactu_developer_dict(),
                "FechaHoraHusoGenRegistro": self._get_verifactu_registration_date(),
                "TipoHuella": "01",  # SHA-256
                "Huella": self.verifactu_hash,
            }
        )
        if self.verifactu_state == "sent_w_errors":
            inv_dict.update(
                {
                    "Subsanacion": "S",
                    # "RechazoPrevio": "X",
                    "Huella": self._set_subsanation_verifactu_hash(),
                    #"Encadenamiento": self._get_chaining_invoice_dict(), #infobit
                }
            )
        registroAlta.setdefault("RegistroAlta", inv_dict)
        return registroAlta

    def _get_chaining_invoice_dict(self):
        """TODO
        si no es el primer registro, hay que enviar el registro anterior.
        Cuando sepamos cuál es el registro anterior"""
        if self.last_verifactu_invoice_entry_id and self.last_verifactu_invoice_entry_id.previous_invoice_entry_id:
           prev_invoice = self.last_verifactu_invoice_entry_id.previous_invoice_entry_id.document_id
        else:
         if self.verifactu_previous_document_id:
           prev_invoice = self.verifactu_previous_document_id
         else:
           prev_invoice = self._get_previous_invoice()
        if prev_invoice:
           #raise Warning(prev_invoice)
           valores = {
               "RegistroAnterior": {
                    "IDEmisorFactura": prev_invoice._get_verifactu_issuer(),
                    "NumSerieFactura": prev_invoice._get_document_serial_number(),
                    "FechaExpedicionFactura": prev_invoice._change_date_format(
                        prev_invoice._get_document_date()),
                    "Huella": prev_invoice.verifactu_hash, 
               }
           }
           return valores
        else:
           return {"PrimerRegistro": "S"}

    def _get_verifactu_taxes_and_total(self):
        self.ensure_one()
        taxes_dict = {}
        taxes_dict.setdefault("DetalleDesglose", [])
        tax_lines = [] #self._get_aeat_tax_info()
        document_date = self._get_document_fiscal_date()
        taxes_S1 = self._get_verifactu_taxes_map(["S1"], document_date)
        taxes_S2 = self._get_verifactu_taxes_map(["S2"], document_date)
        taxes_N1 = self._get_verifactu_taxes_map(["N1"], document_date)
        taxes_N2 = self._get_verifactu_taxes_map(["N2"], document_date)
        taxes_req = self._get_verifactu_taxes_map(["RE"], document_date)
        taxes_not_in_total = self._get_verifactu_taxes_map(
            ["TaxNotIncludedInTotal"], document_date
        )
        base_not_in_total = self._get_verifactu_taxes_map(
            ["BaseNotIncludedInTotal"], document_date
        )
        excluded_taxes = taxes_not_in_total + base_not_in_total
        breakdown_taxes = taxes_S1 + taxes_S2 + taxes_N1 + taxes_N2
        not_in_amount_total = 0.0
        not_in_taxes = 0.0
        tax_dict = {}
        vtax = []
        for tax_line in self.tax_line_ids: #self.invoice_line:
            #BUSCAR IMPUESTO
            imp = self.env['account.tax'].search([('name', '=', tax_line.name)])
            if imp:
               #for tax_line in inv_line.invoice_line_tax_id:
               if imp in taxes_not_in_total:
                   not_in_amount_total += tax_line["amount"]
               elif imp in base_not_in_total:
                   not_in_amount_total += tax_line["base"]
               if imp in breakdown_taxes:
                   operation_type = self._get_operation_type(
                    imp, taxes_S1, taxes_S2, taxes_N1, taxes_N2
                   )
                   tax_dict = {
                    "Impuesto": self.verifactu_tax_key,
                    "ClaveRegimen": self.verifactu_registration_key_code,
                    "CalificacionOperacion": operation_type,
                   }
                   tax_dict["BaseImponibleOimporteNoSujeto"] = tax_line.base
                   if tax_line.invoice_id.type == 'out_refund':
                      tax_dict["BaseImponibleOimporteNoSujeto"] = -tax_line.base
                   if operation_type not in ['N1', 'N2']:
                    tax_dict["TipoImpositivo"] = imp.amount
                    tax_dict["CuotaRepercutida"] = round(tax_line.amount,2)
                    if tax_line.invoice_id.type == 'out_refund':
                       tax_dict["CuotaRepercutida"] = -round(tax_line.amount,2)
                    #RECARGO DE EQUIVALENCIA 
                    reqeq = self.env['account.fiscal.position.tax'].search([('tax_src_id', '=', imp.id), ('tax_dest_id', 'in', taxes_req.ids)])
                    if reqeq:
                      tax_line_req = self.tax_line_ids.filtered(lambda x: x.name == reqeq[0].tax_dest_id.name)
                      if tax_line_req:
                         tipo_recargo = reqeq[0].tax_dest_id.amount
                         cuota_recargo = round(tax_line_req[0].amount,2)
                         if tax_line.invoice_id.type == 'out_refund':
                            cuota_recargo = -round(tax_line_req[0].amount,2)
                         tax_dict['TipoRecargoEquivalencia'] = tipo_recargo
                         tax_dict['CuotaRecargoEquivalencia'] = cuota_recargo
                   taxes_dict["DetalleDesglose"].append(tax_dict)
               elif imp in excluded_taxes:
                not_in_taxes += tax_line["amount"]
               elif imp not in taxes_req:
                raise UserError(_("%s tax is not mapped to Verifactu." % imp.name)) 
        if self.type == 'out_refund':
           amount_tax = -(self.amount_tax - not_in_taxes)
        else:
           amount_tax = self.amount_tax - not_in_taxes
        amount_total = self.amount_total_signed - not_in_amount_total
        return (
            taxes_dict,
            amount_tax,
            amount_total,
        )


    def _get_operation_type(self, tax_line, taxes_S1, taxes_S2, taxes_N1, taxes_N2):
        """
        S1	Operación Sujeta y No exenta - Sin inversión del sujeto pasivo.
        S2	Operación Sujeta y No exenta - Con Inversión del sujeto pasivo
        N1	Operación No Sujeta artículo 7, 14, otros.
        N2	Operación No Sujeta por Reglas de localización.
        """
        tax = tax_line #["tax"]
        if tax in taxes_S1:
            return "S1"
        elif tax in taxes_S2:
            return "S2"
        elif tax in taxes_N1:
            return "N1"
        elif tax in taxes_N2:
            return "N2"
        return "S1"
    
    def _get_verifactu_receiver_dict(self):
        self.ensure_one()
        receiver = self._aeat_get_partner()
        country_code, identifier_type, identifier = receiver._parse_aeat_vat_info()
        if identifier:
            identifier = "".join(e for e in identifier if e.isalnum()).upper()
        else:
            identifier = "NO_DISPONIBLE"
            identifier_type = "06"
        if identifier_type == "":
            return {"IDDestinatario": {"NombreRazon": receiver.name, "NIF": identifier}}
        if (
            receiver._map_aeat_country_code(country_code)
            in receiver._get_aeat_europe_codes()
        ):
            identifier = country_code + identifier
        return {
            "IDDestinatario": {
                "NombreRazon": receiver.name,
                "IDOtro": {
                    "CodigoPais": receiver.country_id.code,
                    "IDType": identifier_type,
                    "ID": identifier,
                },
            }
        }

    #CANCELAR FACTURA EN VERIFACTU
    def cancel_verifactu(self):
        raise NotImplementedError

    #OBTENCION QR URL INVOICE AEAT
    def _compute_verifactu_qr_url(self):
        for move in self: #.filtered(lambda m: m.inalterable_hash):
            if move.company_id.verifactu_test:
               base_url = move.company_id.tax_agency_id.verifactu_qr_base_url_test_address
            else:
               base_url = move.company_id.tax_agency_id.verifactu_qr_base_url
            _taxes_dict, _amount_tax, amount_total = self._get_verifactu_taxes_and_total()
            urlqrinvoice = base_url
            if move.company_id.vat:
               urlqrinvoice += "nif=" + move.company_id.vat[2:]
            if move.number:
               urlqrinvoice += "&numserie=" + move.number
            if move.date_invoice:
               urlqrinvoice += "&fecha=" + move._change_date_format(self._get_document_date())
            if move.amount_total:
               urlqrinvoice += "&importe=" + str(amount_total)
            move.verifactu_qr_url = urlqrinvoice
            # Generar el código QR
            """qr = qrcode.QRCode(
               version=1,  # Tamaño del QR: 1 es el más pequeño
               error_correction=qrcode.constants.ERROR_CORRECT_L,  # Nivel de corrección de errores
               box_size=10,  # Tamaño de los cuadros
               border=4,  # Tamaño del borde
            )"""
            qr = qrcode.QRCode(
                border=0, error_correction=qrcode.constants.ERROR_CORRECT_M
            )
            qr.add_data(urlqrinvoice)
            qr.make() #(fit=True)
            # Crear una imagen del QR
            qr_image = qr.make_image() #fill_color="black", back_color="white")
            # Guardar la imagen en un archivo
            temp_file = io.BytesIO() #StringIO()
            qr_image.save(temp_file)
            qr_image = b64encode(temp_file.getvalue()).decode('utf-8')
            move.write({'qr_image': qr_image})
            #move.qr_image = img.save("codigo_qr.png")

    def _compute_verifactu_macrodata(self):
        for document in self:
            document.verifactu_macrodata = (
                float_compare(
                    abs(document._get_verifactu_amount_total()),
                    VERIFACTU_MACRODATA_LIMIT,
                    precision_digits=2,
                )
                >= 0
            )

    @api.model
    def _get_verifactu_tax_keys(self):
        return self.env["account.fiscal.position"]._get_verifactu_tax_keys()

    def _connect_params_aeat(self, mapping_key):
        self.ensure_one()
        agency = self.company_id.tax_agency_id
        if not agency:
            # We use spanish agency by default to keep old behavior with
            # ir.config parameters. In the future it might be good to reinforce
            # to explicitly set a tax agency in the company by raising an error
            # here.
            agency = self.env.ref("l10n_es_aeat_verifactu.aeat_tax_agency_spain")
        return agency._connect_params_verifactu(mapping_key, self.company_id)

    def _get_aeat_verifactu_header(self, tipo_comunicacion=False, cancellation=False):
        """Builds VERIFACTU send header

        :param tipo_comunicacion String 'A0': new reg, 'A1': modification
        :param cancellation Bool True when the communitacion es for document
            cancellation
        :return Dict with header data depending on cancellation
        """
        self.ensure_one()
        if not self.company_id.vat:
            raise ValidationError(
                _("No VAT configured for the company '{}'").format(self.company_id.name)
            )
        header = {
            "ObligadoEmision": {
                "NombreRazon": self.company_id.name[0:120],
                "NIF": self.company_id.partner_id.vat[2:] #_parse_aeat_vat_info()[2],
            },
        }
        registration_date = self.verifactu_registration_date
        if (
            self.verifactu_state == "sent_w_errors"
            and registration_date < fields.Datetime.now()
            and self.verifactu_send_error[:4] == "2004"
        ):
            header.update({"RemisionVoluntaria": {"Incidencia": "S"}})
        return header

    def _get_verifactu_invoice_dict(self):
        self.ensure_one()
        inv_dict = {}
        mapping_key = self._get_mapping_key()
        if mapping_key in ["out_invoice", "out_refund"]:
            inv_dict = self._get_verifactu_invoice_dict_out(cancel=False)
        else:
            raise NotImplementedError
        """round_by_keys(
            inv_dict,
            [
                "BaseImponibleOimporteNoSujeto",
                "CuotaRepercutida",
                "TipoRecargoEquivalencia",
                "CuotaRecargoEquivalencia",
                "CuotaTotal",
                "ImporteTotal",
                "BaseRectificada",
                "CuotaRectificada",
            ],
        )"""
        return inv_dict


    def _get_verifactu_developer_dict(self):
        """
        Datos del desarrollador del sistema informático
        """
        if not self.company_id.verifactu_developer_id:
            raise UserError(
                _("Please, configure the verifactu developer in your company")
            )
        developer = self.company_id.verifactu_developer_id
        spanish_companies = (
            self.env["res.company"]
            .sudo()
            .search_count(
                [("partner_id.country_id", "=", self.env.ref("base.es").id)]
            )
        )
        return {
            "NombreRazon": developer.name,
            "NIF": developer.vat,
            "NombreSistemaInformatico": developer.sif_name,
            "IdSistemaInformatico": developer.sif_id,
            "Version": developer.version,
            "NumeroInstalacion": developer.installation_number,
            "TipoUsoPosibleSoloVerifactu": "S",
            "TipoUsoPosibleMultiOT": "S",
            "IndicadorMultiplesOT": "S" if spanish_companies > 1 else "N",
            "IDOtro": {
                "IDType": "",
                "ID": "",
            },
        }

    def _get_previous_invoice(self):
        prev_invoice = self.search([('state', 'in', ['open', 'paid']),
        ('company_id', '=', self.company_id.id), ('verifactu_hash', '!=', False), ('verifactu_state', '!=', 'not_sent'), ('create_date', '<', self.create_date)], order='create_date desc')
        if prev_invoice:
           return prev_invoice[0]
        #else:
        #   return []
        #   raise ValidationError(_("No se encuentra factura previa"))

    def _aeat_check_exceptions(self):
        """Inheritable method for exceptions control when sending veri*FACTU invoices."""
        res = super()._aeat_check_exceptions()
        if self.company_id.verifactu_enabled and not self.verifactu_enabled:
            raise UserError(_("This invoice is not veri*FACTU enabled."))
        return res

    def _change_date_format(self, date):
        datetimeobject = fields.Date.from_string(date)
        new_date = datetimeobject.strftime(VERIFACTU_DATE_FORMAT)
        return new_date

    def _get_verifactu_version(self):
        return VERIFACTU_VERSION


    def _is_aeat_simplified_invoice(self):
        """Inheritable method to allow control when an
        invoice are simplified or normal"""
        partner = self._aeat_get_partner()
        return partner.aeat_simplified_invoice

    def _get_verifactu_jobs_field_name(self):
        raise NotImplementedError


    def confirm_verifactu_one_document(self):
        self.sudo()._send_document_to_verifactu()

    def _send_document_to_verifactu(self):
        for document in self.filtered(
            lambda i: i.state in self._get_verifactu_valid_document_states()
        ):
            if document.verifactu_state == "not_sent":
                tipo_comunicacion = "A0"
            else:
                tipo_comunicacion = "A1"
            header = document._get_aeat_verifactu_header(tipo_comunicacion)
            doc_vals = {
                "verifactu_header_sent": json.dumps(header, indent=4),
            }
            try: 
                inv_dict = document._get_verifactu_invoice_dict()
            except Exception as fault:
                raise ValidationError(fault) #from fault
            try:
                mapping_key = document._get_mapping_key()
                serv = document._connect_verifactu(mapping_key)
                doc_vals["verifactu_content_sent"] = json.dumps(inv_dict, indent=4)
                if mapping_key in ["out_invoice", "out_refund"]:
                    res = serv.RegFactuSistemaFacturacion(header, inv_dict)
                res_line = res["RespuestaLinea"][0]
                if res["EstadoEnvio"] == "Correcto":
                    doc_vals.update(
                        {
                            "verifactu_state": "sent",
                            "verifactu_csv": res["CSV"],
                            "verifactu_send_failed": False,
                        }
                    )
                elif (
                    res["EstadoEnvio"] == "ParcialmenteCorrecto"
                    and res_line["EstadoRegistro"] == "AceptadoConErrores"
                ):
                    doc_vals.update(
                        {
                            "verifactu_state": "sent_w_errors",
                            "verifactu_csv": res["CSV"],
                            "verifactu_send_failed": True,
                        }
                    )
                else:
                    doc_vals["verifactu_send_failed"] = True
                doc_vals["verifactu_return"] = res
                send_error = False
                if res_line["CodigoErrorRegistro"]:
                    send_error = "{} | {}".format(
                        str(res_line["CodigoErrorRegistro"]),
                        str(res_line["DescripcionErrorRegistro"]),
                    )
                doc_vals["verifactu_send_error"] = send_error
                document.write(doc_vals)
            except Exception as fault:
                self.env.cr.rollback()
                new_cr = Registry(self.env.cr.dbname).cursor()
                env = api.Environment(new_cr, self.env.uid, self.env.context)
                document = env[document._name].browse(document.id)
                doc_vals.update(
                    {
                        "verifactu_send_failed": True,
                        "verifactu_send_error": repr(fault)[:200],
                        "verifactu_return": repr(fault),
                        "verifactu_content_sent": json.dumps(inv_dict, indent=4),
                    }
                )
                document.write(doc_vals)
                new_cr.commit()
                new_cr.close()
                raise ValidationError(fault)

    def _connect_verifactu(self, mapping_key):
        # de momento no puedo el _connect_aeat del aeat_mixin porque si no pongo
        # forbid_entities en settings del Client da error de entities forbiden
        self.ensure_one()
        public_crt = self.env['ir.config_parameter'].sudo().get_param(
            'l10n_es_aeat_verifactu.publicCrt', False)
        private_key = self.env['ir.config_parameter'].sudo().get_param(
            'l10n_es_aeat_verifactu.privateKey', False)
        params = self._connect_params_aeat(mapping_key)
        parser = etree.XMLParser(resolve_entities=False)
        session = Session()
        #raise Warning(params)
        session.cert = (public_crt, private_key)
        transport = Transport(session=session) #, xml_headers={'Content-Type': 'application/soap+xml'})
        history = HistoryPlugin()
        settings = Settings(forbid_entities=False)
        wsdl=params["wsdl"]
        client = Client(
            wsdl=params["wsdl"],
            transport=transport,
            plugins=[history],
            settings=settings,
        )
        return self._bind_service(client, params["port_name"], params["address"])

    def _bind_service(self, client, port_name, address=None):
        self.ensure_one()
        service = client._get_service("sfVerifactu")
        port = client._get_port(service, port_name)
        address = address or port.binding_options["address"]
        raise Warning(address)
        return client.create_service(port.binding.name, address)

    @api.multi
    def map_verifactu_tax_template(self, tax_template, mapping_taxes):
        """Adds a tax template -> tax id to the mapping.
        Adapted from account_chart_update module.

        :param self: Single invoice record.
        :param tax_template: Tax template record.
        :param mapping_taxes: Dictionary with all the tax templates mapping.
        :return: Tax template current mapping
        """
        self.ensure_one()
        if not tax_template:
            return self.env['account.tax']
        if mapping_taxes.get(tax_template):
            return mapping_taxes[tax_template]
        # search inactive taxes too, to avoid re-creating
        # taxes that have been deactivated before
        tax_obj = self.env['account.tax'].with_context(active_test=False)
        criteria = ['|',
                    ('name', '=', tax_template.name),
                    ('description', '=', tax_template.name)]
        if tax_template.description:
            criteria = ['|'] + criteria
            criteria += [
                '|',
                ('description', '=', tax_template.description),
                ('name', '=', tax_template.description),
            ]
        criteria += [('company_id', '=', self.company_id.id)]
        mapping_taxes[tax_template] = tax_obj.search(criteria)
        return mapping_taxes[tax_template]


    @api.model
    def _get_verifactu_taxes_map(self, codes, date):
        """Return the codes that correspond to verifactu map line codes.

        :param codes: List of code strings to get the mapping.
        :param date: Date to map
        :return: Recordset with the corresponding codes
        """
        map_obj = self.env["aeat.verifactu.map"].sudo().with_context(active_test=False)
        taxes = self.env['account.tax']
        """verifactu_map = map_obj.search(
            [
                "|",
                ("date_from", "<=", date),
                ("date_from", "=", False),
                "|",
                ("date_to", ">=", date),
                ("date_to", "=", False),
            ],
            limit=1,
        )"""
        verifactu_map = self._get_verifactu_map(date)
        mapping_taxes = {}
        tax_templates = verifactu_map.map_lines.filtered(
            lambda x: x.code in codes
        ).taxes
        for tax_template in tax_templates:
            taxes += self.map_verifactu_tax_template(tax_template, mapping_taxes)
        return taxes

    @api.depends("fiscal_position_id")
    def _compute_verifactu_tax_key(self):
        for document in self:
            document.verifactu_tax_key = (
                document.fiscal_position_id.verifactu_tax_key or "01"
            )

    @api.depends("fiscal_position_id")
    def _compute_verifactu_registration_key(self):
        for document in self:
            if document.fiscal_position_id:
                key = document.fiscal_position_id.verifactu_registration_key
                if key:
                    document.verifactu_registration_key = key
            else:
                domain = [
                    ("code", "=", "01"),
                    (
                        "verifactu_tax_key",
                        "=",
                        "iva",
                    ),
                ]
                verifactu_key_obj = self.env["aeat.verifactu.registration.keys"]
                document.verifactu_registration_key = verifactu_key_obj.search(
                    domain, limit=1
                )

    @api.depends("verifactu_registration_key")
    def _compute_verifactu_registration_key_code(self):
        for record in self:
            record.verifactu_registration_key_code = (
                record.verifactu_registration_key.code
            )

    @api.model
    def _get_verifactu_batch(self):
        try:
            return int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("l10n_es_aeat_verifactu.verifactu_batch", "50")
            )
        except ValueError as e:
            raise UserError(
                _(
                    "The value in l10n_es_aeat_verifactu.verifactu_batch "
                    "system parameter must be an integer. Please, check the "
                    "value of the parameter."
                )
            ) from e
